class VideoStream:
    def __init__(self, filename):
        self.filename = filename
        try:
            self.file = open(filename, 'rb')
        except:
            raise IOError
        self.frameNum = 0
        
    def nextFrame(self):
        """Get next frame."""
        start_pos = self.file.tell()
        
        data = self.file.read(5)
        if not data: 
            return None

        try:
            framelength = int(data)
            
            frame_data = self.file.read(framelength)
            self.frameNum += 1
            return frame_data

        except ValueError:
            self.file.seek(start_pos)
            
            full_data = b''
            while True:
                chunk = self.file.read(4096)
                if not chunk:
                    break
                
                full_data += chunk
                
                end_pos = full_data.find(b'\xff\xd9')
                
                if end_pos != -1:
                    frame_len = end_pos + 2
                    frame_data = full_data[:frame_len]
                    
                    self.file.seek(start_pos + frame_len)
                    
                    self.frameNum += 1
                    return frame_data
            
            return None if not full_data else full_data
        
    def frameNbr(self):
        """Get frame number."""
        return self.frameNum