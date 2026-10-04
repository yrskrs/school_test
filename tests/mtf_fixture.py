"""Small synthetic MyTestX 10.2 records; no private test or Windows binary."""
import struct
import zlib


def u32(value):
    return struct.pack('<I', value)


def string(value):
    data = value.encode('utf-16-le')
    return u32(len(data) // 2) + data


def rtf(value):
    return r'{\rtf1\ansi\ansicpg1251 ' + value + '}' if value else ''


def array(values, strings=False):
    return u32(len(values)) + b''.join(string(v) if strings else u32(v) for v in values)


def bitmap():
    pixels = b'\x00\x00\xff\x00' * 4
    dib = struct.pack('<IiiHHIIiiII', 40, 2, 2, 1, 32, 0, len(pixels), 0, 0, 0, 0) + pixels
    return struct.pack('<2sIHHI', b'BM', 14 + len(dib), 0, 0, 54) + dib


def picture():
    dib = bitmap()[14:]
    record = struct.pack('<IH', (28 + len(dib)) // 2, 0x0f43) + bytes(22) + dib
    wmf = struct.pack('<HHHIHIH', 1, 9, 0x300, (18 + len(record) + 6) // 2, 0, len(record) // 2, 0)
    wmf += record + struct.pack('<IH', 3, 0)
    return r'{\rtf1{\pict\wmetafile8 ' + wmf.hex() + '}}'


def record(kind, payload, image='', title=None):
    return (bytes([kind]) + u32(5) + string(rtf(title or f'Question {kind}'))
            + b''.join(string(v) for v in ['', '', '', '', image, ''])
            + u32(2) + bytes(20) + u32(0) + b'\x01' + payload + string('') + b'\x00')


def encrypt_body(body, version='10.2.0.2'):
    plain = 'MyTestX'.encode('utf-16-le') + string(version) + zlib.compress(body)
    state, output = 0x0101, bytearray()
    for value in plain:
        key = state & 0xff
        output.append(value ^ key)
        state = (((value + key) & 0xff) * 0x6ca9 + 0xce1c) & 0xffff
    return bytes(output)


def build_mtf(extra_record=None):
    choice = array([rtf('Wrong'), rtf('Right'), ''], True) + array([0, 1, 0])
    multi = array([rtf('First'), rtf('Second')], True) + array([1, 1])
    seq = array([rtf('Second'), rtf('First')], True) + array([2, 1])
    match = (array([rtf('A'), rtf('B')], True) + array([2, 1])
             + array([rtf('Right B'), rtf('Right A')], True))
    records = [record(1, choice), record(2, multi), record(3, seq), record(4, match),
               record(7, string('2,0\r\n2.0') + bytes(2)),
               record(6, b'\x01' + struct.pack('<ddIB', 16, 16, 0, 1)),
               record(8, u32(1) + u32(4) + struct.pack('<8i', 0, 0, 2, 0, 2, 2, 0, 2), 'picture.bmp'),
               record(4, array([rtf('A'), rtf('B')], True) + array([2, 1])
                      + array([picture(), picture()], True), title='Picture matching')]
    if extra_record:
        records.append(extra_record)
    body = b''.join(string(s) for s in ['Synthetic MTF', 'Author', 'Email', rtf('Description')])
    body += string('{00000000-0000-0000-0000-000000000001}')
    body += u32(1) + u32(0) + string('Topic') + string('Description') + u32(1) + b'\x01'
    body += bytes(24) + u32(len(records)) + b''.join(records)
    body += u32(1) + string('picture.bmp') + u32(len(bitmap())) + bitmap()
    return encrypt_body(body)
