from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk, ImageFile
import socket, threading, sys, traceback, os
import time
import queue

from RtpPacket import RtpPacket

ImageFile.LOAD_TRUNCATED_IMAGES = True 

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

class Client:
    # --- TRẠNG THÁI ---
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT
    
    # --- LOẠI REQUEST ---
    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3
    
    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.createWidgets()
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename
        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0
        self.connectToServer()
        self.rtpSocket = None
    
        self.FRAME_BUFFER_SIZE = 120
        self.PRE_BUFFER_SIZE = 30
        self.frame_buffer = queue.Queue(maxsize=self.FRAME_BUFFER_SIZE)
        self.is_buffering = True
        
        self.current_frame_data = b''
        self.curr_rtp_seq_num = -1
        self.expected_seq_num = -1
        
        self.stat_total_packets = 0
        self.stat_lost_packets = 0
        self.stat_total_bytes = 0
        self.stat_frames_received = 0
        self.stat_frames_lost = 0
        
        self.session_start_time = None
        self.session_end_time = None
        
        self.fps_frame_count = 0
        self.fps_start_time = time.time()
        self.last_total_bytes = 0
        self.display_fps = 0.0
        self.display_data_rate = 0.0
        self.calc_start_time = 0      
        self.calc_end_time = 0        
        self.calc_pause_time = 0      
        self.temp_pause_start = 0     

        
    def createWidgets(self):
        self.setup = Button(self.master, width=20, padx=3, pady=3)
        self.setup["text"] = "Setup"
        self.setup["command"] = self.setupMovie
        self.setup.grid(row=1, column=0, padx=2, pady=2)
        
        self.start = Button(self.master, width=20, padx=3, pady=3)
        self.start["text"] = "Play"
        self.start["command"] = self.playMovie
        self.start.grid(row=1, column=1, padx=2, pady=2)
        
        self.pause = Button(self.master, width=20, padx=3, pady=3)
        self.pause["text"] = "Pause"
        self.pause["command"] = self.pauseMovie
        self.pause.grid(row=1, column=2, padx=2, pady=2)
        
        self.teardown = Button(self.master, width=20, padx=3, pady=3)
        self.teardown["text"] = "Teardown"
        self.teardown["command"] = self.exitClient
        self.teardown.grid(row=1, column=3, padx=2, pady=2)
        
        self.label = Label(self.master, bg="black")
        self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)
        
        self.statsLabel = Label(self.master, text="State: INIT", font=("Consolas", 10), anchor=W, justify=LEFT)
        self.statsLabel.grid(row=2, column=0, columnspan=4, sticky=W+E, padx=5, pady=2)

    def setupMovie(self):
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)
    
    def playMovie(self):
        if self.state == self.READY:
            # Start RTP listening thread if not already started
            if self.rtpSocket and not hasattr(self, 'listenning_thread_started'):
                t = threading.Thread(target=self.listenRtp, daemon=True)
                t.start()
                self.listenning_thread_started = True
            
            self.sendRtspRequest(self.PLAY)
            self.fps_start_time = time.time()
            self.fps_frame_count = 0
            
            if self.temp_pause_start > 0:
                paused_duration = time.time() - self.temp_pause_start
                self.calc_pause_time += paused_duration
                self.temp_pause_start = 0
                print(f"DEBUG: Resumed. Added {paused_duration:.2f}s to pause time.")
            
            if self.session_start_time is None:
                self.session_start_time = time.time()

    def pauseMovie(self):
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)
            self.temp_pause_start = time.time()
    
    def exitClient(self):
        self.sendRtspRequest(self.TEARDOWN)
        self.session_end_time = time.time()
        wait_start = time.time()
        
        while self.teardownAcked == 0 and (time.time() - wait_start < 1.0):
            time.sleep(0.05)

        self.printFinalStatistics()
        
        self.master.destroy()
        try:
            for f in os.listdir('.'):
                if f.startswith(CACHE_FILE_NAME):
                    os.remove(f)
        except:
            pass

    def printFinalStatistics(self):
        print("\n" + "="*70)
        print(" FINAL SESSION STATISTICS")
        print("="*70)
        
        print(f"\nPacket Statistics:")
        print(f"  Total Packets Received: {self.stat_total_packets}")
        print(f"  Total Packets Lost: {self.stat_lost_packets}")
        
        total_expected = self.stat_total_packets + self.stat_lost_packets
        if total_expected > 0:
            loss_rate = (self.stat_lost_packets / total_expected) * 100
            print(f"  Packet Loss Rate: {loss_rate:.2f}%")
        
        print(f"\nFrame Statistics:")
        print(f"  Total Frames Received: {self.stat_frames_received}")
        print(f"  Total Frames Lost: {self.stat_frames_lost}")
        
        total_frames_expected = self.stat_frames_received + self.stat_frames_lost
        if total_frames_expected > 0:
            frame_loss_rate = (self.stat_frames_lost / total_frames_expected) * 100
            print(f"  Frame Loss Rate: {frame_loss_rate:.2f}%")
        
        print(f"\nData Statistics:")
        print(f"  Total Data Received: {self.stat_total_bytes / (1024*1024):.2f} MB")
        
        if self.calc_end_time > 0 and self.calc_start_time > 0:
            duration = self.calc_end_time - self.calc_start_time - self.calc_pause_time
            
            if duration > 0:
                print(f"  Calculation Duration: {duration:.2f} seconds")
                avg_bitrate = (self.stat_total_bytes * 8) / (duration * 1000)
                avg_data_rate = self.stat_total_bytes / (duration * 1024)
                
                print(f"  Average Bitrate: {avg_bitrate:.2f} kbps")
                print(f"  Average Data Rate: {avg_data_rate:.2f} KB/s")
                
                if self.stat_frames_received > 0:
                    avg_fps = self.stat_frames_received / duration
                    print(f"  Average FPS: {avg_fps:.2f}")
            else:
                print("  Duration too short to calculate statistics.")
        else:
            print("  Video did not start playing or no data received.")
        
        print("="*70 + "\n")

    def listenRtp(self):
        print("Client: Started listening for RTP packets")
        while True:
            try:
                if self.teardownAcked == 1:
                    break
                
                data = self.rtpSocket.recv(65535)  # Increased buffer for HD
                
                if data:
                    self.calc_end_time = time.time()
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)
                    
                    receivedSeqNum = rtpPacket.seqNum()
                    marker = rtpPacket.getMarker()
                    payload = rtpPacket.getPayload()
                    
                    self.stat_total_packets += 1
                    self.stat_total_bytes += len(data)
                    
                    # Detect packet loss
                    if self.expected_seq_num != -1:
                        if receivedSeqNum != self.expected_seq_num:
                            packets_lost = (receivedSeqNum - self.expected_seq_num) % 65536
                            if packets_lost < 1000:  
                                self.stat_lost_packets += packets_lost

                                if len(self.current_frame_data) > 0:
                                    self.stat_frames_lost += 1
                                self.current_frame_data = b''
                    
                    self.expected_seq_num = (receivedSeqNum + 1) % 65536
                    
                    self.current_frame_data += payload
                    
                    if marker == 1:
                        if len(self.current_frame_data) > 0:

                            path = self.writeFrame(self.current_frame_data, receivedSeqNum)
                            
                            if not self.frame_buffer.full():
                                self.frame_buffer.put(path)
                                self.stat_frames_received += 1
                            else:
                                try:
                                    os.remove(path)
                                except:
                                    pass
                        
                        self.current_frame_data = b''
      
            except socket.timeout:
                continue
            except Exception as e:
                if self.teardownAcked == 1:
                    break
                print(f"RTP Error: {e}")
                    
    def writeFrame(self, data, frameNum):
        cachename = CACHE_FILE_NAME + str(self.sessionId) + "_" + str(frameNum) + CACHE_FILE_EXT
        try:
            with open(cachename, "wb") as file:
                file.write(data)
        except:
            pass
        return cachename
   
    def consumeBuffer(self):
        start_processing_time = time.time()
        
        if self.state != self.PLAYING:
            return
        if self.requestSent == self.PAUSE:
            return

        current_size = self.frame_buffer.qsize()
        now = time.time()
        time_diff = now - self.fps_start_time
        
        # Update FPS and data rate every second
        if time_diff >= 1.0:
            self.display_fps = self.fps_frame_count / time_diff
            bytes_received_in_window = self.stat_total_bytes - self.last_total_bytes
            self.display_data_rate = (bytes_received_in_window / 1024.0) / time_diff
            self.fps_frame_count = 0
            self.fps_start_time = now
            self.last_total_bytes = self.stat_total_bytes
        
        # Calculate loss rate
        loss_rate = 0.0
        total_expected = self.stat_total_packets + self.stat_lost_packets
        if total_expected > 0:
            loss_rate = (self.stat_lost_packets / total_expected) * 100

        # Pre-buffering logic
        if self.is_buffering:
            if current_size < self.PRE_BUFFER_SIZE:
                percent = int((current_size / self.PRE_BUFFER_SIZE) * 100)
                self.statsLabel.config(
                    text=f"Buffering... {current_size}/{self.PRE_BUFFER_SIZE} frames ({percent}%) | Loss: {loss_rate:.2f}%"
                )
                self.master.after(50, self.consumeBuffer)
                return
            else:
                self.is_buffering = False
                if self.calc_start_time == 0:
                    self.calc_start_time = time.time()
                    print(f"DEBUG: Start Timer at {self.calc_start_time}")
        
        # Check if buffer is empty (rebuffering needed)
        if current_size == 0:
            self.is_buffering = True
            self.statsLabel.config(text="Rebuffering...")
            self.master.after(20, self.consumeBuffer)
            return

        # Play frame from buffer
        if not self.frame_buffer.empty():
            imageFile = self.frame_buffer.get()
            self.updateMovie(imageFile)
            
            self.fps_frame_count += 1
            
            try:
                os.remove(imageFile)
            except:
                pass
            
            # Update stats display
            frame_loss_rate = 0.0
            total_frames = self.stat_frames_received + self.stat_frames_lost
            if total_frames > 0:
                frame_loss_rate = (self.stat_frames_lost / total_frames) * 100
            
            stat_text = (f"FPS: {self.display_fps:.1f} | "
                        f"Rate: {self.display_data_rate:.1f} KB/s | "
                        f"Buffer: {current_size}/{self.FRAME_BUFFER_SIZE} | "
                        f"Pkt Loss: {loss_rate:.2f}% | "
                        f"Frame Loss: {frame_loss_rate:.2f}%")
            self.statsLabel.config(text=stat_text)
            
            target_delay = 50 
            
            if current_size < self.PRE_BUFFER_SIZE / 2:
                target_delay = 60  
            elif current_size > self.PRE_BUFFER_SIZE * 2:
                target_delay = 40  
                
            processing_duration = (time.time() - start_processing_time) * 1000 
            
            actual_delay = target_delay - processing_duration
            
            if actual_delay < 1:
                actual_delay = 1
                
            self.master.after(int(actual_delay), self.consumeBuffer)
    
    def updateMovie(self, imageFile):
        try:
            image = Image.open(imageFile)
            width, height = image.size
            
            # Target display size for HD
            if width > 1280 or height > 720:
                # HD video
                target_width = 1280
                target_height = 720
                resample = Image.BILINEAR  
            else:
                # SD video
                target_width = 640
                target_height = 360
                resample = Image.BILINEAR
            
            image = image.resize((target_width, target_height), resample)
            photo = ImageTk.PhotoImage(image)
            self.label.configure(image=photo, height=target_height)
            self.label.image = photo
        except Exception as e:
            print(f"Error displaying frame: {e}")
        
    def connectToServer(self):
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except:
            tkMessageBox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' % self.serverAddr)
    
    def sendRtspRequest(self, requestCode):
        request = ""
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply, daemon=True).start()
            self.rtspSeq += 1
            request = "SETUP {} RTSP/1.0\nCSeq: {}\nTransport: RTP/UDP; client_port={}\n".format(
                self.fileName, self.rtspSeq, self.rtpPort
            )
            self.requestSent = self.SETUP
        
        elif requestCode == self.PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = "PLAY {} RTSP/1.0\nCSeq: {}\nSession: {}\n".format(
                self.fileName, self.rtspSeq, self.sessionId
            )
            self.requestSent = self.PLAY
        
        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            self.rtspSeq += 1
            request = "PAUSE {} RTSP/1.0\nCSeq: {}\nSession: {}\n".format(
                self.fileName, self.rtspSeq, self.sessionId
            )
            self.requestSent = self.PAUSE
            
        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            self.rtspSeq += 1
            request = "TEARDOWN {} RTSP/1.0\nCSeq: {}\nSession: {}\n".format(
                self.fileName, self.rtspSeq, self.sessionId
            )
            self.requestSent = self.TEARDOWN
        else:
            return
        
        self.rtspSocket.send(request.encode())
        print('\nData sent:')
        for line in request.split('\n'):
            if line.strip():
                print(f"C: {line}")
    
    def recvRtspReply(self):
        while True:
            try:
                reply = self.rtspSocket.recv(1024)
                if reply:
                    decoded_reply = reply.decode("utf-8")
                    print('\nData received:')
                    for line in decoded_reply.split('\n'):
                        if line.strip():
                            print(f"S: {line}")
                    self.parseRtspReply(decoded_reply)
                
                if self.requestSent == self.TEARDOWN:
                    self.rtspSocket.shutdown(socket.SHUT_RDWR)
                    self.rtspSocket.close()
                    break
            except:
                break
    
    def parseRtspReply(self, data):
        lines = data.split('\n')
        if len(lines) < 3:
            return
        try:
            status_code = int(lines[0].split(' ')[1])
            seqNum = int(lines[1].split(' ')[1])
            session = int(lines[2].split(' ')[1])
        except:
            return
        
        if seqNum == self.rtspSeq and status_code == 200:
            if self.sessionId == 0:
                self.sessionId = session
            
            if self.sessionId == session:
                if self.requestSent == self.SETUP:
                    print("Transition: INIT -> READY")
                    self.state = self.READY
                    self.openRtpPort()
                    
                elif self.requestSent == self.PLAY:
                    print("Transition: READY -> PLAYING")
                    self.state = self.PLAYING
                    self.current_frame_data = b''
                    self.expected_seq_num = -1
                    self.is_buffering = True
                    self.consumeBuffer()
                    
                elif self.requestSent == self.PAUSE:
                    print("Transition: PLAYING -> READY")
                    self.state = self.READY
                    
                elif self.requestSent == self.TEARDOWN:
                    print("Transition: -> INIT")
                    self.state = self.INIT
                    self.teardownAcked = 1
    
    def openRtpPort(self):
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2*1024*1024)  # 2MB buffer
        self.rtpSocket.settimeout(0.5)
        try:
            self.rtpSocket.bind(('', self.rtpPort))
            print(f"RTP socket bound to port {self.rtpPort}")
        except:
            tkMessageBox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' % self.rtpPort)

    def handler(self):
        self.pauseMovie()
        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            self.playMovie()