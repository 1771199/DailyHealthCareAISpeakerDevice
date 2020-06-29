from __future__ import division
from threading import Thread
from multiprocessing import Process
import paho.mqtt.client as paho
import re
import sys
import socket
import ssl
import pygame
from google.cloud import speech
from google.cloud.speech import enums
from google.cloud.speech import types
import pyaudio
from mutagen.mp3 import MP3
from six.moves import queue
from triggerword import snowboy as sb
import snowboydecoder
import time
import os
import signal
import argparse
import struct
import datetime
import numpy as np
import soundfile
import queue
sys.path.append(os.path.join(os.path.dirname(__file__), '../../binding/python'))
sys.path.append(os.path.join(os.path.dirname(__file__), '../../resources/util/python'))

from pulsesensor import Pulsesensor
# [END import_libraries]
 
# Audio recording parameters
RATE = 16000
CHUNK = int(RATE / 10)  # 100ms
 
HOST = '192.168.0.2'
PORT = 8888

msg =' ' 
msg_flag = False

global canCall

interrupted = False

global client_socket
global playing
pygame.init()
pygame.mixer.init()


# -----------------------------Wake-Up Word----------------------------- #

def interrupt_callback():
    global interrupted
    return interrupted

def callback():  # Wake-Up Word 인식 시 실행되는 콜백 함수.
    global detector
    global canCall
    canCall = False
    detector.terminate()
    global fileName
    # Wake-Up Word 인식 시 "부르셨어요?" 음성 출력.
    fileName = 'call.mp3'
    playMP3File()
    interactive()

def snowBoy(): # Wake-UP Word 인식을 담당하는 함수.
    model = "jangsooya.pmdl"
    global detector
    global canCall
    detector = snowboydecoder.HotwordDetector(model, sensitivity = 0.5) # 민감도를 0.5로 하여 Wake-Up Word detector 생성.
    while True:
        if(canCall):  # 음성이 출력 중일 때 Wake-Up Word를 인식하는 것을 방지.
            detector.start(detected_callback = callback, interrupt_check = interrupt_callback, sleep_time = 0.03)

#--------------------------------------------------------------#
#--------------------MIC INPUT --------------------#

class MicrophoneStream(object):
    """Opens a recording stream as a generator yielding the audio chunks."""
    def __init__(self, rate, chunk):
        self._rate = rate
        self._chunk = chunk
 
        self._buff = queue.Queue()
        self.closed = True
 
    def __enter__(self):
        self._audio_interface = pyaudio.PyAudio()
        self._audio_stream = self._audio_interface.open(
            format=pyaudio.paInt16,
            channels=1, rate=self._rate,
            input=True, frames_per_buffer=self._chunk,
            stream_callback=self._fill_buffer,
        )
        self.closed = False
        return self
 
    def __exit__(self, type, value, traceback):
        self._audio_stream.stop_stream()
        self._audio_stream.close()
        self.closed = True
        self._buff.put(None)
 
    def _fill_buffer(self, in_data, frame_count, time_info, status_flags):
        """Continuously collect data from the audio stream, into the buffer."""
        self._buff.put(in_data)
        return None, pyaudio.paContinue
 
    def generator(self):
        global startTime
        startTime = time.time()
        while not self.closed:
            chunk = self._buff.get()
            if chunk is None:
                return
            data = [chunk]
 
            while True:
                endTime = time.time()
                reStartTimer = endTime-startTime
                try:
                    chunk = self._buff.get(block=False)
                    if chunk is None:
                        return
                    data.append(chunk)
                except queue.Empty:
                    break
            yield b''.join(data)
            if (reStartTimer > 55):
               break
    def end(self):
        self._audio_stream.stop_stream()
        self._audio_stream.close()
        self.closed = True
        self._buff.put(None)
        self._audio_interface.terminate()

# [END audio_stream]
 
def listen_print_loop(responses):
    global listen
    num_chars_printed = 0
    for response in responses:
        if not response.results:
            continue
        result = response.results[0]
        if not result.alternatives:
            continue
        transcript = result.alternatives[0].transcript
        overwrite_chars = ' ' * (num_chars_printed - len(transcript)) 
        if not result.is_final:
            sys.stdout.write(transcript + overwrite_chars + '\r')
            sys.stdout.flush()
            num_chars_printed = len(transcript)
 
        else:
           global msg_flag
           global deviceID
           print(transcript + overwrite_chars)
           global stream
           stream.end()
           Send('speech', str(transcript+overwrite_chars))
           msg_flag = False
           while not msg_flag: # msg_flag로 여러 개의 메시지를 처리한 후 더 이상 메시지를 받지 않을 때 처리를 멈춤.
               continue
           if re.search(r'\b(exit|quit)\b', transcript, re.I):
               print('Exiting..')
               break
           num_chars_printed = 0

#--------------------Sound--------------------#
 
def synthesize_text(text):  # Goolge Cloud Speech API를 활용해 text를 음성으로 변환하여 feedback.mp3에 저장.
    """Synthesizes speech from the input string of text."""
    from google.cloud import texttospeech
    done = False
    while not done:
        try:
            client = texttospeech.TextToSpeechClient()
            input_text = texttospeech.types.SynthesisInput(text=text)
            voice = texttospeech.types.VoiceSelectionParams(
                language_code='ko-KR',
                name='ko-KR-Standard-A',
                ssml_gender=texttospeech.enums.SsmlVoiceGender.FEMALE)
            audio_config = texttospeech.types.AudioConfig(
                audio_encoding=texttospeech.enums.AudioEncoding.MP3)
            response = client.synthesize_speech(input_text, voice, audio_config)
            with open('feedback.mp3', 'wb') as out:
                out.write(response.audio_content)
            done = True
        except:
            pass

def playMP3File():  # 음성 출력에 사용하는 함수.
      global micOff
      micOff = True
      global fileName
      global playing
      pygame.init()
      pygame.mixer.init()
      pygame.mixer.music.set_volume(1)
      audio = MP3(fileName)
      pygame.mixer.music.load(fileName)
      while playing:
         continue
      playing = True
      pygame.mixer.music.play()
      time.sleep(audio.info.length+1) # 음성이 모두 출력될 때까지 대기.
      playing = False

def audioProcess():  # text를 음성으로 변환한 후 재생하는 함수.
    global msg
    synthesize_text(msg)
    playMP3File()

#--------------------Message Processing---------------------#

def messageProcessing():  # 서버에서 받은 메시지를 유형에 따라 처리하는 함수.
    global msg
    global micOff
    global fileName
    global msg_flag
    global listen
    global messageQ
    global canCall
    if not messageQ.qsize() == 0:
        msg = messageQ.get()
        alarmTime = None
        if 'Output' in msg and '다시' in msg:  # 서버에서 사용자의 발화를 정확히 인식하지 못 했을 때 '다시 말씀해 주세요'라는 음성을 출력하고 마이크 입력을 다시 시작.
            fileName = 'repeat.mp3'
            playMP3File()
            msg_flag = True
            listen = False
            micOff = False

        elif 'Output' in msg:  # 서버에서 받은 feedback 메시지를 가공하여 음성 출력.
            msg = msg.split('-')[1]
            fileName = 'feedback.mp3'
            audioProcess()

        elif 'No Sleep Data' in msg:  # 전 날의 수면 데이터가 없을 때, '어제는 몇 시에 주무셨나요?'라는 음성을 출력하고 마이크 입력을 다시 시작.
            fileName = 'NoSleepTime.mp3'
            playMP3File()
            msg_flag = True
            listen = False
            micOff = False

        elif 'Measure Pulse' in msg:  # 심박수 측정 의도를 확인하였을 때 실행하는 함수. 심박수 측정 시작을 알리는 음성 출력 후 Pulse Sensor로 심박 수를 측정.
            msg_flag = True
            listen = False
            micOff = True
            fileName = 'StartPulse.mp3'
            playMP3File()
            heartBeat()
            canCall = True
        
        elif 'No Pulse Data' in msg:  # 심박수 측정 데이터가 없을 때 이를 알림
            fileName = 'NoPulseData.mp3'
            playMP3File()
            canCall = True       

        elif 'End' in msg in msg:  # 더 이상 받을 메시지가 없을 때 서버에서 받은 평균 시간 기록을 txt로 저장하고 마이크 입력을 중단. 
            msg_flag = True
            listen = False
            micOff = True
            canCall = True
            if alarmTime is not None:
               f = open('TimeAverage.txt', 'w')
               f.write(alarmTime)
               f.close()
               setAlarmTime()

        elif 'Exist' in msg:  # 심박수 측정 데이터가 존재할 때.
            msg_flag = True            
            listen = False
            micOff = True
            canCall = True

        elif 'Time Average' in msg: # 평균 시간 데이터를 입력받았을 때 이를 변수에 저장. File I/O에 프로세스가 지체되는 것을 방지하기 위해 이는 'End' 메시지 수신 시 수행.
            alarmTime = msg.split('-')[1]

        else:       # 안드로이드 어플리케이션에서 수신한 메시지
            synthesize_text(msg)
            fileName = 'feedback.mp3'
            playMP3File()
            canCall = True
   
#--------------------main--------------------#
def interactive():  # Google Cloud Speech API로 사용자의 발화를 text로 변환하여 서버에 전송.
    global interacting
    global micOff
    global socket_connect
    global listen
    socket_connect = False
    interacting = False
    language_code = 'ko_KR'  # a BCP-47 language tag
    micOff = False

    while not micOff:           # 마이크가 켜져 있는 동안 ... micOff = True: 마이크 꺼짐.
       listen = True
       global client
       global config
       global streaming_config
       client = speech.SpeechClient()
       config = types.RecognitionConfig(
           encoding=enums.RecognitionConfig.AudioEncoding.LINEAR16,
           sample_rate_hertz=RATE,
           language_code=language_code)
       streaming_config = types.StreamingRecognitionConfig(
           config=config, single_utterance = True,
           interim_results=True)
       global stream
       with MicrophoneStream(RATE, CHUNK) as stream:
            print("Mic ON")
            audio_generator = stream.generator()
            requests = (types.StreamingRecognizeRequest(audio_content=content)
                        for content in audio_generator)
            done = False
            while not done:
                try:
                    responses = client.streaming_recognize(streaming_config, requests)
                    done = True
                except:
                    pass 
            while listen:
                listen_print_loop(responses)
          
def sendAlarm(meal): # 서버에 알림 메시지를 전송하는 함수.
    global msg_flag
    global client_socket
    global deviceID
    Send('check', meal)
    msg_flag = False
    while not msg_flag:
        continue

def Send(tag, body): # 소켓 통신으로 메시지를 가공하여 서버에 전송.
    global client_socket
    global deviceID
    global info
    if body == "alarm":
        send = tag + "-" + deviceID
    else:
        send = tag + "-"+ deviceID + "-" + body
    data = send.encode()
    length = len(data)
    done = False
    while not done:
        try:
            client_socket.sendall(length.to_bytes(4, byteorder="little"))
            client_socket.sendall(data)
            done = True
        except:
            try:
                client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                client_socket.connect((HOST, PORT))
                id = info.encode()
                length = len(id)
                client_socket.sendall(length.to_bytes(4, byteorder="little"))
                client_socket.sendall(id)
            except: pass

def heartBeat(): # Pulse Sensor로 심박수를 측정하는 함수.
    global fileName
    global deviceID
    p = Pulsesensor()
    p.startAsyncBPM()
    count = 0
    bpmSum = 0
    tryNum = 0
    Done = False
    startTime = time.time()
    while not Done: # 측정이 정확하게 될 때까지 반복.
       nowTime = time.time()
       while int(nowTime - startTime) <= 12: # 12초 동안 측정.
            bpm = p.BPM
            if bpm >= 60 and bpm < 84:  # Pulse Sensor가 비정상적인 값을 인식할 때, 평균 계산에 적용하지 않기 위함.
                bpmSum += bpm
                count += 1 # 정확한 측정이 몇 번 수행되었는지 확인.
            nowTime = time.time()
            continue
       if count is not 0: # 정확한 측정이 한 번이라도 수행되었을 때.
          p.stopAsyncBPM() # 측정 중단.
          value = bpmSum / count # 평균 값 계산.
          Done = True # 측정 완료를 알림.
          intvalue = str(round(value, 1)).split('.')[0] # 소수점 첫째 자리에서 반올림 하여 int형으로 변환.
       else:
          fileName = "PulseAgain.mp3" # 측정이 정확하지 않을 때 다시 측정하겠다는 알림 출력.
          playMP3File()
          tryNum +=1
          startTime = time.time()
    Send('pulse', intvalue) # 측정 값을 서버에 전송.
    global msg_flag
    while not msg_flag:
       continue

#-------------------------------Thread Method--------------------------------#

def Recieve(): # 소켓 통신으로 서버 메시지를 수신하는 함수.
    global client_socket
    global fileName
    global deviceID
    global data
    global messageQ
    global info
    global sendID
    sendID = False
    info = "id:"+deviceID
    messageQ = queue.Queue()
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_socket.connect((HOST, PORT))
    id = info.encode()
    length = len(id)
    client_socket.sendall(length.to_bytes(4, byteorder="little"))
    client_socket.sendall(id)
    sendID = True
    while True:  # 데이터의 유실을 막기 위해 Decoding을 진행한 후 Queue에 저장.
        data = client_socket.recv(4)
        length = int.from_bytes(data, "little")
        data = client_socket.recv(length)
        message = data.decode("utf-8")
        messageQ.put(message)

def dataQueue(): # Queue에 메시지가 담겨 있을 때, 이를 처리함.
   global messageQ
   global msg_flag
   messageQ = queue.Queue()
   while True:
      messageProcessing()

def timer(): # TimeAverage.txt의 평균 시간에 따른 식전 약 복용 정보 요청과 일정 시간의 심박수 측정 확인 알림을 서버에 전송.
    global baTime
    global laTime
    global daTime
    global h_oneTimeOnly
    global b_oneTimeOnly
    global l_oneTimeOnly
    global d_oneTimeOnly
    global deviceID
    global client_socket
    global msg_flag
    global sendID
    h_oneTimeOnly = False
    b_oneTimeOnly = False
    l_oneTimeOnly = False
    d_oneTimeOnly = False
    while True:
       if sendID:
          now = datetime.datetime.now()
          if now.hour == baTime.hour and now.minute == baTime.minute:
             if b_oneTimeOnly is not True:
                sendAlarm("breakfast")
                b_oneTimeOnly = True
          if now.hour == laTime.hour and now.minute == laTime.minute:
             if l_oneTimeOnly is not True:
                sendAlarm("lunch")
                l_oneTimeOnly = True
          if now.hour == daTime.hour and now.minute == daTime.minute:
             if d_oneTimeOnly is not True:
                sendAlarm("dinner")
                d_oneTimeOnly = True 
          if now.hour == 15  and now.minute == 30:
             if h_oneTimeOnly is not True:
                Send("pulse", "alarm")
                msg_flag = False
                while not msg_flag:
                   continue
                h_oneTimeOnly = True
        
#----------------------------------------------------------------------------------------------------#

def initDevice():  # 전원 열결 시 txt 파일로 Device ID를 설정.
    global deviceID
#    global client_socket
    global playing
    playing = False
    f = open('DeviceID.txt', 'r')
    deviceID = f.readline()
    f.close()

def setAlarmTime():  # TimeAverage.txt 파일에 담긴 평균 시간 데이터를 활용하여 식사시간 평균 30분 전의 알림 시간을 설정.
    global baTime
    global laTime
    global daTime
    global h_oneTimeOnly
    global b_oneTimeOnly
    global l_oneTimeOnly
    global d_oneTimeOnly
    global deviceID
    h_oneTimeOnly = False
    b_oneTimeOnly = False
    l_oneTimeOnly = False
    d_oneTimeOnly = False
    f = open('TimeAverage.txt', 'r')
    alarmTime = f.readline()
    f.close()
    breakfast = alarmTime.split('/')[2]
    lunch = alarmTime.split('/')[3]
    dinner = alarmTime.split('/')[4]
    wakeUp = alarmTime.split('/')[0]
    sleep = alarmTime.split('/')[1]

    bhour = breakfast.split(":")[0]
    if bhour == '24':
       bhour = '0'
    bmin = breakfast.split(":")[1]

    lhour = lunch.split(":")[0]
    if lhour == '24':
       lhour = '0'
    lmin = lunch.split(":")[1]

    dhour = dinner.split(":")[0]
    if dhour == '24':
        dhour = '0'
    dmin = dinner.split(":")[1]

    bTime = datetime.time(int(bhour),int(bmin))
    lTime = datetime.time(int(lhour), int(lmin))
    dTime = datetime.time(int(dhour), int(dmin))

    if int(bmin) < 30:
        bmin = str(60-(30-int(bmin)))
        if bhour == '0' or bhour == '00':
          bhour = '24'
        bhour = str(int(bhour) - 1)
    else:
       bmin = str(int(bmin)-30)
    baTime = datetime.time(int(bhour), int(bmin))
    if int(lmin) <= 30:
       lmin = str(60-(30-int(lmin)))
       if lhour == '0' or lhour == '00':
         lhour = '24'
       lhour = str(int(lhour)-1)
    else: lmin = str(int(lmin)-30)
    laTime = datetime.time(int(lhour), int(lmin))
    if int(dmin) <= 30:
       dmin = str(60-(30-int(dmin)))
       if dhour == '00' or dhour == '0':
         dhour = '24'
       dhour = str(int(dhour) - 1)
    else: dmin = str(int(dmin)-30)
    daTime = datetime.time(int(dhour), int(dmin))
    alarmTime = None

if __name__ == '__main__':
    initDevice()
    setAlarmTime()
    global realdata
    realdata = " "
    global canCall
    canCall = True
    interactthread = Thread(target=snowBoy)
    recievethread = Thread(target=Recieve)
    timerthread = Thread(target=timer)
    queueThread = Thread(target=dataQueue)

    recievethread.start()
    interactthread.start()
    timerthread.start()
    queueThread.start()
    
    interactthread.join()
    recievethread.join()
    timerthread.join()
    queueThread.join()
