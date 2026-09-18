# /// script
# dependencies = [
#     "pillow",
#     "pyserial",
# ]
# ///

import sys
import time
from PIL import Image
import serial

PORT = "COM3"
BAUDRATE = 115200

def process_image(image_path):
    img = Image.open(image_path)
    
    # T02 exact width is 384 dots (48 bytes per line)
    target_width = 384
    wpercent = (target_width / float(img.size[0]))
    target_height = int(float(img.size[1]) * float(wpercent))
    
    img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
    img = img.convert("1")
    
    width, height = img.size
    bytes_per_line = width // 8
    
    raster_data = bytearray()
    for y in range(height):
        for x_byte in range(bytes_per_line):
            byte = 0
            for bit in range(8):
                x = x_byte * 8 + bit
                # 0 = black dot, 1 = white dot
                if img.getpixel((x, y)) == 0:
                    byte |= (1 << (7 - bit))
            raster_data.append(byte)
            
    return raster_data, bytes_per_line, height

def send_to_printer(raster_data, bytes_per_line, height):
    print(f"Connecting to {PORT}...")
    try:
        ser = serial.Serial(PORT, baudrate=BAUDRATE, timeout=2, write_timeout=5)
    except Exception as e:
        print(f"Failed to open {PORT}: {e}")
        return

    ser.reset_input_buffer()
    ser.reset_output_buffer()

    print("Initializing M02/T02 protocol...")
    # 1. Header initialization sequence (vivier/phomemo-tools standard)
    ser.write(b'\x1b\x40')         # ESC @: Initialize printer
    ser.write(b'\x1b\x61\x01')     # ESC a 1: Center alignment
    ser.write(b'\x1f\x11\x02\x04') # Proprietary config packet
    time.sleep(0.1)

    # 2. Send image chunks with pacing to prevent buffer overflow
    # T02 max chunk height is 255 lines per block marker
    chunk_size = 255
    offset = 0
    
    print(f"Streaming {height} lines of raster data...")
    while offset < height:
        lines_to_send = min(chunk_size, height - offset)
        chunk_bytes = raster_data[offset * bytes_per_line : (offset + lines_to_send) * bytes_per_line]
        
        mode = 0x00
        xL = bytes_per_line & 0xFF
        xH = (bytes_per_line >> 8) & 0xFF
        
        # Intermediate blocks must declare 0xFF height per protocol spec if full chunk
        header_height = 0xFF if lines_to_send == 255 else lines_to_send
        yL = header_height & 0xFF
        yH = (header_height >> 8) & 0xFF
        
        header = bytes([0x1d, 0x76, 0x30, mode, xL, xH, yL, yH])
        
        # Send header + chunk data with a small pacing delay
        ser.write(header + chunk_bytes)
        ser.flush()
        time.sleep(0.02) # Give the Nuvoton serial buffer time to empty
        
        offset += lines_to_send

    print("Sending footer and flushing print buffer...")
    # 3. Footer sequence to close job and feed paper past tear bar
    ser.write(b'\x1b\x64\x02')     # ESC d 2: Feed lines
    ser.write(b'\x1b\x64\x02')
    ser.write(b'\x1f\x11\x08')     # Stop/Flush packets
    ser.write(b'\x1f\x11\x0e')
    ser.write(b'\x1f\x11\x07')
    ser.write(b'\x1f\x11\x09')
    ser.flush()

    ser.close()
    print("Print job successfully completed!")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python print_t02_paced.py <image.png>")
        sys.exit(1)
        
    image_file = sys.argv[1]
    data, b_line, img_h = process_image(image_file)
    send_to_printer(data, b_line, img_h)
    