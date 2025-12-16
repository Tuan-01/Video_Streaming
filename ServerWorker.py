from random import randint
import sys, traceback, threading, socket
import time

from VideoStream import VideoStream
from RtpPacket import RtpPacket

class ServerWorker:
    SETUP = 'SETUP'
    PLAY = 'PLAY'
    PAUSE = 'PAUSE'
    TEARDOWN = 'TEARDOWN'
    
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    OK_200 = 0
    FILE_NOT_FOUND_404 = 1
    CON_ERR_500 = 2
 
    RTP_PAYLOAD_MAX = 1400
    
    TARGET_FPS = 20
    FRAME_INTERVAL = 1.0 / TARGET_FPS  
    
    clientInfo = {}
    
    def __init__(self, clientInfo):
        self.clientInfo = clientInfo
        self.rtpSeqNum = 0
        
    def run(self):
        threading.Thread(target=self.recvRtspRequest).start()
    
    def recvRtspRequest(self):
        connSocket = self.clientInfo['rtspSocket'][0]
        while True:
            try:
                data = connSocket.recv(256)
                if data:
                    print("RTSP Request received:\n" + data.decode("utf-8"))
                    self.processRtspRequest(data.decode("utf-8"))
            except:
                break
    
    def processRtspRequest(self, data):
        request = data.split('\n')
        line1 = request[0].split(' ')
        requestType = line1[0]
        filename = line1[1]
        seq = request[1].split(' ')
        
        if requestType == self.SETUP:
            if self.state == self.INIT:
                print("Processing SETUP\n")
                try:
                    self.clientInfo['videoStream'] = VideoStream(filename)
                    self.state = self.READY
                except IOError:
                    self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
                    return
                
                self.clientInfo['session'] = randint(100000, 999999)
                
                try:
                    line_with_port = [l for l in request if 'client_port' in l][0]
                    part_after_eq = line_with_port.split('client_port=')[1]
                    port_str = part_after_eq.split('-')[0].split(';')[0].strip()
                    self.clientInfo['rtpPort'] = port_str
                    print(f"Client RTP Port: {self.clientInfo['rtpPort']}")
                except:
                    print("Error parsing RTP Port, defaulting to 25000")
                    self.clientInfo['rtpPort'] = "25000"

                self.replyRtsp(self.OK_200, seq[1])
        
        elif requestType == self.PLAY:
            if self.state == self.READY:
                print("Processing PLAY\n")
                self.state = self.PLAYING
                self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.replyRtsp(self.OK_200, seq[1])
                self.clientInfo['event'] = threading.Event()
                self.clientInfo['worker'] = threading.Thread(target=self.sendRtp)
                self.clientInfo['worker'].start()
        
        elif requestType == self.PAUSE:
            if self.state == self.PLAYING:
                print("Processing PAUSE\n")
                self.state = self.READY
                self.clientInfo['event'].set()
                self.replyRtsp(self.OK_200, seq[1])
        
        elif requestType == self.TEARDOWN:
            print("Processing TEARDOWN\n")
            self.clientInfo['event'].set()
            self.replyRtsp(self.OK_200, seq[1])
            if 'rtpSocket' in self.clientInfo:
                self.clientInfo['rtpSocket'].close()
    
    def sendRtp(self):
        frame_count = 0
        stream_start_time = time.time()
        
        while True:
            self.clientInfo['event'].wait(0.0001)
            if self.clientInfo['event'].isSet():
                break

            data = self.clientInfo['videoStream'].nextFrame()
            
            if data:
                frame_count += 1
                frameNumber = self.clientInfo['videoStream'].frameNbr()
                address = self.clientInfo['rtspSocket'][1][0]
                port = int(self.clientInfo['rtpPort'])
                
                # Fragment frame into multiple RTP packets for HD support
                total_len = len(data)
                curr_pos = 0
                packet_count = 0
                
                while curr_pos < total_len:
                    chunk = data[curr_pos : curr_pos + self.RTP_PAYLOAD_MAX]
                    curr_pos += self.RTP_PAYLOAD_MAX
                    packet_count += 1
                    
                    if curr_pos >= total_len:
                        marker = 1
                    else:
                        marker = 0
                    
                    try:
                        rtpPacket = self.makeRtp(chunk, frameNumber, marker)
                        self.clientInfo['rtpSocket'].sendto(
                            rtpPacket,
                            (address, port)
                        )
                    except Exception as e:
                        print(f"RTP Send Error: {e}")
                        break
                
                target_time = stream_start_time + (frame_count * self.FRAME_INTERVAL)
                current_time = time.time()
                sleep_time = target_time - current_time
                
                if sleep_time > 0:
                    time.sleep(sleep_time)
                elif sleep_time < -0.05:  
                    print(f"Warning: Falling behind by {-sleep_time*1000:.1f}ms at frame {frameNumber}")
            else:
                elapsed = time.time() - stream_start_time
                actual_fps = frame_count / elapsed if elapsed > 0 else 0
                print(f"\nEnd of stream")
                break

    def makeRtp(self, payload, frameNbr, marker=0):
        """
        Create RTP packet with proper header
        """
        version = 2
        padding = 0
        extension = 0
        cc = 0
        pt = 26  
        ssrc = 123456
        seqnum = self.rtpSeqNum
        self.rtpSeqNum = (self.rtpSeqNum + 1) % 65536  
        
        rtpPacket = RtpPacket()
        rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload)
        return rtpPacket.getPacket()
    
    def replyRtsp(self, code, seq):
        if code == self.OK_200:
            reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
            connSocket = self.clientInfo['rtspSocket'][0]
            connSocket.send(reply.encode())
        elif code == self.FILE_NOT_FOUND_404:
            print("404 NOT FOUND")
        elif code == self.CON_ERR_500:
            print("500 CONNECTION ERROR")