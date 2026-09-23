# /// script
# dependencies = [
#     "pymupdf",
#     "pillow",
#     "pyserial",
# ]
# ///

import sys
import time
from PIL import Image
import fitz  # PyMuPDF
import serial

PORT = "COM3"
BAUDRATE = 115200
TARGET_WIDTH = 384  # Exact width of T02 print head in dots

def pdf_to_scaled_image(pdf_path):
    doc = fitz.open(pdf_path)
    page_images = []

    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        zoom = 2.0  
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        
        wpercent = (TARGET_WIDTH / float(img.size[0]))
        target_height = int(float(img.size[1]) * float(wpercent))
        img = img.resize((TARGET_WIDTH, target_height), Image.Resampling.LANCZOS)
        
        page_images.append(img)
        
    doc.close()

    if not page_images:
        raise ValueError("The PDF contains no readable pages.")

    total_height = sum(img.size[1] for img in page_images)
    padding = 15 if len(page_images) > 1 else 0
    total_height += padding * (len(page_images) - 1)

    combined_img = Image.new("1", (TARGET_WIDTH, total_height), 1)
    
    current_y = 0
    for img in page_images:
        bw_img = img.convert("1")
        combined_img.paste(bw_img, (0, current_y))
        current_y += img.size[1] + padding

    return combined_img

def send_image_to_printer(img):
    width, height = img.size
    bytes_per_line = width // 8
    
    raster_data = bytearray()
    for y in range(height):
        for x_byte in range(bytes_per_line):
            byte = 0
            for bit in range(8):
                x = x_byte * 8 + bit
                if img.getpixel((x, y)) == 0:
                    byte |= (1 << (7 - bit))
                if byte == 0x0A:
                    byte = 0x14
            raster_data.append(byte)

    print(f"Connecting to {PORT}...")
    try:
        ser = serial.Serial(PORT, baudrate=BAUDRATE, timeout=2, write_timeout=5)
    except Exception as e:
        print(f"Failed to open {PORT}: {e}")
        return

    ser.reset_input_buffer()
    ser.reset_output_buffer()

    print("Initializing T02 protocol...")
    ser.write(b'\x1b\x40')
    ser.write(b'\x1b\x61\x01')
    ser.write(b'\x1f\x11\x02\x04')
    time.sleep(1)

    chunk_size = 100
    offset = 0
    
    print(f"Streaming {height} lines of thermal raster data...")
    while offset < height:
        lines_to_send = min(chunk_size, height - offset)
        chunk_bytes = raster_data[offset * bytes_per_line : (offset + lines_to_send) * bytes_per_line]
        
        mode = 0x00
        xL = bytes_per_line & 0xFF
        xH = (bytes_per_line >> 8) & 0xFF
        header_height = 0xFF if lines_to_send == 255 else lines_to_send
        yL = header_height & 0xFF
        yH = (header_height >> 8) & 0xFF
        
        header = bytes([0x1d, 0x76, 0x30, mode, xL, xH, yL, yH])
        ser.write(header + chunk_bytes)
        ser.flush()
        time.sleep(2)
        
        offset += lines_to_send

    print("Sending footer and feeding paper...")
    ser.write(b'\x1b\x64\x02')
    ser.write(b'\x1b\x64\x02')
    ser.write(b'\x1f\x11\x08')
    ser.write(b'\x1f\x11\x0e')
    ser.write(b'\x1f\x11\x07')
    ser.write(b'\x1f\x11\x09')
    ser.flush()

    ser.close()
    print("PDF print job successfully completed!")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run print_pdf_t02.py <document.pdf>")
        sys.exit(1)
        
    pdf_file = sys.argv[1]
    print(f"Processing PDF: {pdf_file}")
    final_image = pdf_to_scaled_image(pdf_file)
    send_image_to_printer(final_image)
    