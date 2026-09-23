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
    print("IMG wpercent:", wpercent, " target_height:", target_height)
    
    if (wpercent!=1.0):
        img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
    img = img.convert("1")
    
    width, height = img.size
    print("Img width:", width, " height:", height)
        
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
                if byte == 0x0A:
                    byte = 0x14
            raster_data.append(byte)
            
    return raster_data, bytes_per_line, height

def raster_to_bw_art(raster_data: bytes, bytes_per_line: int, height: int) -> str:
    """
    1-bpp packed raster (bytes_per_line == 48 → 384 px wide)
    → exactly 192 characters per line.
    Each character covers 2×2 source pixels (no aspect-ratio compensation).
    """
    assert bytes_per_line == 48

    SRC_W  = 384
    OUT_W  = 192
    CELL_W = 2
    CELL_H = 2          # same as width → no aspect compensation

    SYMBOLS = [
        (" ",  0x0000000000000000),   # empty
        ("▀",  0xffffffff00000000),   # upper half
        ("▄",  0x00000000ffffffff),   # lower half
        ("█",  0xffffffffffffffff),   # full
        ("▌",  0xf0f0f0f0f0f0f0f0),   # left half
        ("▐",  0x0f0f0f0f0f0f0f0f),   # right half
        ("▖",  0x00000000f0f0f0f0),   # lower-left (corrected from 0x0f...)
        ("▗",  0x000000000f0f0f0f),   # lower-right (corrected from 0xf0...)
        ("▘",  0xf0f0f0f000000000),   # upper-left (corrected from 0x0f...)
        ("▝",  0x0f0f0f0f00000000),   # upper-right (corrected from 0xf0...)
        ("▚",  0xf0f0f0f00f0f0f0f),   # diagonal
        ("▞",  0x0f0f0f0ff0f0f0f0),   # other diagonal
    ]

    def get_pixel(x: int, y: int) -> int:
        if x < 0 or y < 0 or x >= SRC_W or y >= height:
            return 0
        byte = raster_data[y * bytes_per_line + (x >> 3)]
        return (byte >> (7 - (x & 7))) & 1

    def cell_bitmap(cx: int, cy: int) -> int:
        """
        Build an 8×8 bitmap from a 2×2 source window.
        Left pixel → left half of glyph, right pixel → right half.
        Top row of source → top half of glyph, bottom row → bottom half.
        """
        # sample the 2×2 block
        tl = get_pixel(cx,     cy)
        tr = get_pixel(cx + 1, cy)
        bl = get_pixel(cx,     cy + 1)
        br = get_pixel(cx + 1, cy + 1)

        bm = 0
        for row in range(8):
            # top 4 glyph rows ← top source row, bottom 4 ← bottom source row
            left  = tl if row < 4 else bl
            right = tr if row < 4 else br
            for col in range(8):
                val = left if col < 4 else right
                if val:
                    bm |= 1 << (63 - (row * 8 + col))
        return bm

    def best_symbol(bm: int) -> str:
        best_ch = " "
        best_dist = 65
        for ch, glyph in SYMBOLS:
            dist = bin(bm ^ glyph).count("1")
            if dist < best_dist:
                best_dist = dist
                best_ch = ch
        return best_ch

    lines = []
    y = 0
    while y < height:
        row = []
        for col in range(OUT_W):
            cx = col * CELL_W
            bm = cell_bitmap(cx, y)
            row.append(best_symbol(bm))
        lines.append("".join(row))          # exactly 192 chars
        y += CELL_H

    return "\n".join(lines)

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
    time.sleep(1)

    # 2. Send image chunks with pacing to prevent buffer overflow
    # T02 max chunk height is 255 lines per block marker
    chunk_size = 100
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
        time.sleep(2) # Give the Nuvoton serial buffer time to empty
        
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
    print("B_line:", b_line, " Height:", img_h, " len:", data.count)
    art = raster_to_bw_art(data, b_line, img_h)
    print(art)
    send_to_printer(data, b_line, img_h)
        