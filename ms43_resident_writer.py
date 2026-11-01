"""Temporary MS43 write overlays using the stock DS2 flash bus windows.

Stage ``bootstrap(command)`` at E400 with native78, then stage EA0000E4 at
offset0 and execute79. The79 reply contains manufacturer/device words, little
endian. Native73/7A packets then use the cached overlay until another command
loads one. Only RAM changes; flash IDs are diagnostic, as with stock DS2 writes.
"""


def bootstrap(command, *, software="430069"):
    """Build the bounded RAM adapter for original outer73 or7A.

    The verified 430055/430069 directories identify the copied source, size and
    XOR. Their erase/program overlay bytes are identical at different offsets.
    Native startup002D4A..002D66 supplies ADDRSEL1=1008, BUSCON0=45BF and
    active BUSCON1=858E. Stock DS2 agents write through10h+ and read the low
    flash alias. Resident helpers toggle BUSCON1.10 themselves; adapted data
    polling uses8h+ and its one data-store wrapper temporarily adds8h.

    E400..E515 survives every command used by this workflow: the largest
    original73/7A overlay endsE321. Native78/79 restore the first96 bytes from
    F902 and enterE000; its jump transfers to this adapter before it replaces
    E000 with the original overlay. The unchanged native dispatcher owns all
    framing, replies, caching and subsequent execution.
    """
    if command not in (0x73, 0x7A):
        raise ValueError("Only MS43 outer erase/program overlays are supported")
    source, length, checksum, patches, helper = {
        0x73: (0xAA94, 0x24C, 0x7DC1, (0xE086, 0xE17E, 0xE192), 0xE176),
        0x7A: (0xB586, 0x322, 0xC3AD, (0xE082, 0xE1CC, 0xE1E0), 0xE1C4),
    }[command]
    source += {"430069": 0, "430055": -0xCE}[software]
    manufacturer_cmp = 0xE0A4 if command == 0x73 else 0xE0A0
    code = bytearray()

    def emit(value):
        code.extend(bytes.fromhex(value))

    def word(value):
        code.extend(value.to_bytes(2, "little"))

    def move(register, value):
        code.extend((0xE6, register))
        word(value)

    # Original008366..008398 copy loop and native word-XOR helper.
    move(0xF4, 0xE000)
    move(0xF5, source)
    move(0xF2, length)
    emit("0D02 E945 0841 A002 3DFC")
    move(0xFC, 0xE000)
    move(0xFD, length // 2)
    emit("CA00 D883 46F4")
    word(checksum)
    emit("2D05")
    reject_offset = len(code)
    emit("E7F8B600 F7F802F7 CB00")  # Native B6 error, no flash commands.

    # Independently check every changed operand/instruction before adapting it.
    store = 0xE13C if command == 0x73 else 0xE172
    guards = [(address, 0x20) for address in patches]
    guards.extend(((store, 0x08DC if command == 0x73 else 0x0BDC),
                   (store + 2, 0x47B8 if command == 0x73 else 0x4AB8),
                   (manufacturer_cmp, 0x9148)))
    if command == 0x73:
        guards.append((0xE15A, 10000))
    for address, expected in guards:
        emit("F2F4")
        word(address)
        emit("46F4")
        word(expected)
        relative = (reject_offset - (len(code) + 2)) // 2
        assert -128 <= relative <= 127
        code.extend((0x3D, relative & 255))

    emit("E7F80800 F7F8")
    word(patches[0])  # Data/poll address uses the existing high read alias.
    emit("E7F81000")
    for address in patches[1:]:
        emit("F7F8")
        word(address)  # Autoselect/reset commands use the DS2 write window.
    move(0xF4, 0x00CA)
    emit("F6F4")
    word(store)
    move(0xF4, 0)
    store_wrapper_pointer = len(code) - 2
    emit("F6F4")
    word(store + 2)
    # Stock DS2 uses this command set without identifying the manufacturer.
    # Keep the native autoselect/reset sequence and fixed algorithm selector2.
    move(0xF4, 0x9940)  # CMP R9,R9; the original JE now accepts any vendor.
    emit("F6F4")
    word(manufacturer_cmp)
    if command == 0x73:
        # Older compatible MX29F400C specifies15s maximum sector erase.
        # Native T1 ticks are1ms at24MHz; keep polling, allow up to20s.
        move(0xF4, 20000)
        emit("F6F4 5AE1")
    move(0x0C, 0x1008)
    move(0x86, 0x45BF)
    move(0x8A, 0x818E)
    emit("E7F8")
    word(command)
    emit("F7F8C5FC F78EC6FC")  # Publish native overlay cache, selector0.

    # Probe with the original AMD helper, return actual IDs, restore array mode.
    # Mirror native7A E084/E09E: no ROM interrupt fetch while in autoselect.
    emit("BE88")
    move(0xFC, 0x90)
    emit("CA00")
    word(helper)
    for offset, reply in ((0, 0xF703), (2, 0xF705)):
        move(0xF4, offset)
        emit("E085 DC05 A844 F7F8")
        word(reply)
        emit("7C84 F7F8")
        word(reply + 1)
    move(0xFC, 0xF0)
    emit("CA00")
    word(helper)
    emit("BF88")
    emit("E178 F7F800F7 CB00")  # Existing A0 reply plus four identity bytes.
    code[store_wrapper_pointer:store_wrapper_pointer + 2] = (0xE400 + len(code)).to_bytes(2, "little")
    # Preserve the polling address while routing just the native data store.
    if command == 0x73:
        emit("06F80800 DC08 B847 26F80800 CB00")
    else:
        emit("06FB0800 DC0B B84A 26FB0800 CB00")
    assert len(code) == (278 if command == 0x73 else 260)
    return bytes(code)
