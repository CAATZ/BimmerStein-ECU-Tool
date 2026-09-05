; agent_28f_build.asm - INTEL 28F200 flash agent (soft-BSL). Derived from agent_build.asm:
;   the entire host protocol / comms / CRC / chunk / identify code is
;   BYTE-FOR-BYTE the AMD agent's; ONLY the two flash cores + the erase wrapper were swapped to the
;   Intel 28F command set (0x40 program / 0x20+0xD0 erase / 0x70 status / 0xFF read-array), and a
;   VPP=12V bracket (P2.6 + P3.6) was added around erase + program.
;   For chip=28f200 (Intel A28F200BX-B).  Loads/runs at 0xD800.  Assemble with the C166 assembler.
;   Boot-region writes remain brick-class and require the normal confirmation/recovery discipline.
; = agent_softbsl.asm (CALL/RET->calls/rets) + the v5 cores inlined.
; Loads/runs at 0xD800. Assemble with the C166 assembler.
base 0d800h

; ============================================================================
; agent_softbsl.asm  -  RAM-resident Intel 28F200 soft-BSL flash agent
; ----------------------------------------------------------------------------
; Entered by the 0x9C DS2 stub: copied to ext-SRAM @0xD800, interrupts already
; OFF, runs from RAM. Owns the machine. Speaks the host protocol (Part I/II of
; SOFT_BSL_SPEC.md). Chip-aware via a per-chip jump table (AMD 29F400/29F200 here;
; Intel 28Fxxx = a second cmd-set variant). Uses the v5 AMD sequences
; (program_amd.asm) for the flash primitives.
;
; RAM map: code @0xD800-0xDFFF, RX/program buf @0xE000(256B), scratch @0xE100,
;          stack in internal RAM (SP), regbank (CP).
; ASC0 polled: RX-ready = bit 0xFF6E.7 (S0RIC.7), RX data 0xFEB2 (S0RBUF),
;              TX-ready = bit 0xFF6C.7 (S0TIC.7), TX data 0xFEB0 (S0TBUF).
; K-line is half-duplex: every TX echoes -> discard one RX after each TX byte.
; ============================================================================

; --- scratch (ext SRAM) ---
BUF     EQU 0E000h        ; chunk buffer base, 1024 B (0xE000-0xE3FF)
MARKER  EQU 0E400h        ; cached bank-ID byte
BLFLAG  EQU 0E401h        ; bootloader-write armed flag
HALF    EQU 0E402h        ; 0=bottom(working) 1=top(golden)
ACRC    EQU 0E404h        ; 3-byte stash for the chunk address (a2,a1,a0), folded INTO the chunk
;                           CRC16 so a flipped address byte rejects the chunk before programming.
;                           Lives in the free gap 0xE403-0xE655 between HALF and the v5 vars @0xE656.

; --- bank-ID marker: 4 bytes  A5 5A <half> <~half>  at FILE 0x5FFC (CPU 0x1FFC) ---
;     lives in SA1/param1 (PRESERVED by every full write), in the param1 FF tail
;     after the 0x9C stub. Set at bootloader install. Agent validates A5 5A + ~half.
;     half = 'T'(54h)=top/golden, 'B'(42h)=bottom/working;  FF FF FF FF = blank/uninit.
MARK_OFS EQU 01FFCh       ; agent read address (CPU) = file 0x5FFC
NIBTBL   EQU 0FD60h       ; 16-word nibble CRC table in ZERO-WAIT IRAM (0xFD60-0xFD7F; above the
                          ;   stack@0xFC00-down and clear of PEC@0xFCE0)

; === 29F400-BOTTOM sector map (PRIMARY target; 29F200BB uses the same geometry) =
;  #   chip/file base  size       CPU region        content            policy
;  SA0  0x00000  0x4000  (16K)  0x4000-0x7FFF   boot block          write
;  SA1  0x04000  0x2000  (8K)   0x0000-0x1FFF   param1 = BOOTLOADER  PROTECTED**
;  SA2  0x06000  0x2000  (8K)   0x2000-0x3FFF   param2 = hooks       write
;  SA3  0x08000  0x8000  (32K)  0x8000-0xFFFF   main-low (FF)        write
;  SA4  0x10000  0x10000 (64K)  0x10000-0x1FFFF cal / tune           write (cal here)
;  SA5  0x20000  0x10000 (64K)  0x20000-0x2FFFF program-high         write
;  SA6  0x30000  0x10000 (64K)  0x30000-0x3FFFF program-high         write
;  ** SA1 writable only with the 'W' bootloader arm (--write-bootloader).
;  TOP half = coarse SA7-SA10 (4x64K). This RAM-resident agent may write it;
;  the host must select the coarse geometry and rebuild complete erase sectors.
;  29F200BB = SA0-SA6 identical (single bank). Intel 28Fxxx = different map + 0x20/0xD0 cmds.
; ==============================================================================
SECT_PARAM1 EQU 1         ; SA1 is the protected bootloader sector
; Erase/program use the Intel functional block map selected by the host;
; the superseded AMD sector-table design is intentionally absent.

agent_entry:
        BCLR  PSW.11               ; IEN off (belt+suspenders; stub already did it)
        MOV   SP,#0FC00h           ; stack (internal RAM)  [PIN vs PEC ptrs]
        MOV   CP,#0FA00h           ; register bank
        MOV   r0,#0E600h           ; r0 = software-stack ptr (MOVED from 0xE200: the 1KB CBUF now
                                   ;   spans 0xE000-0xE3FF, so 0xE200 would write INSIDE chunk data
                                   ;   and corrupt it; 0xE600 grows DOWN to 0xE5FC, clear of CBUF
                                   ;   below and the v5 vars @0xE656. program_amd_core push/pops r9.)
        ; INHERIT the loader's ASC0 - do NOT re-init. The 0x43 loader already
        ; runs 8E2 at the firmware's REAL 9600 divisor; asc0_init_9600 rewrote S0BG=77
        ; (assumed fCPU=12MHz) and shifted the baud -> garbled banner (got 0x66 for 0xA5).
        ; Reinitializing S0BG here shifts the baud and corrupts the banner. The
        ; 'B'/set-baud path still updates S0BG when explicitly requested.
        MOVB  RL4,#0A5h            ; banner (sent in the INHERITED ASC0 format)
        calls  tx
        calls  identify             ; read MARKER/HALF from the visible half
        calls  build_nibtbl          ; precompute the nibble CRC table @NIBTBL (once)

main:
        SRVWDT
        calls  rx_block             ; RL4 = command (BLOCKING - idle until the host sends one)
        CMPB  RL4,#'I'  ; 49h
        JMPR  cc_EQ,c_ident
        CMPB  RL4,#'S'  ; 53h  (switch flipped -> re-identify)
        JMPR  cc_EQ,c_switched
        CMPB  RL4,#'B'  ; 42h  (set baud)
        JMPR  cc_EQ,c_baud
        CMPB  RL4,#'W'  ; 57h  (arm bootloader write: 'W' + magic)
        JMPR  cc_EQ,c_armbl
        CMPB  RL4,#'E'  ; 45h  (erase sector)
        JMPR  cc_EQ,c_erase
        CMPB  RL4,#'R'  ; 52h  (reset)
        JMPR  cc_EQ,c_reset
        CMPB  RL4,#'C'  ; 43h  (CHUNK program: 1KB CRC16 bulk -> ~8x fewer turnarounds = reliable)
        JMPR  cc_EQ,c_chunk
        CMPB  RL4,#'K'  ; 4Bh  (CRC-checked READ: n bytes + CRC16 = high-baud read integrity)
        JMPR  cc_NE,c_nak
        jmpa  cc_UC,c_crcread     ; c_crcread is far past the JMPR window -> absolute jump
c_nak:  MOVB  RL4,#0FFh            ; NAK unknown cmd
        calls  tx
        JMPR  cc_UC,main

; ---- identify / switched: re-read the bank-ID marker, report it ----
c_switched:
        calls  debounce             ; settle after a mechanical switch flip
c_ident:
        calls  identify             ; (comms section) -> MARKER/HALF, RL4 = marker byte
        calls  tx
        JMPR  cc_UC,main

; ---- POLICY GUARD: given a target sector in r6, may we erase/program it? ----
; rules:  either visible half -> allow, EXCEPT the low boot-containing sector unless BLFLAG armed
; HALF remains cached for identify/reporting compatibility, but is not a write lock.
; returns RL4=0 ok, RL4=1 denied
policy_check:                       ; in r8/r9 = addr ; out RL4=0 ok / 1 deny (uses r4)
        MOVB  RL4,HALF
        CMPB  RL4,#1
        JMPR  cc_UC,pc_bot          ; TOP and BOTTOM use the same RAM-resident writer
        MOVB  RL4,#1               ; unreachable padding keeps the assembled layout stable
        rets
pc_bot: CMP   r9,#0                ; a2 != 0 -> addr >= 0x10000 -> not bootloader
        JMPR  cc_NE,pc_ok
        CMP   r8,#02000h           ; r8 >= 0x2000 -> not bootloader
        JMPR  cc_NC,pc_ok
        MOVB  RL4,BLFLAG           ; bootloader (addr < 0x2000 = SA1): armed?
        CMPB  RL4,#0A5h
        JMPR  cc_EQ,pc_ok
        MOVB  RL4,#1               ; deny: bootloader not armed
        rets
pc_ok:  MOVB  RL4,#0
        rets

c_armbl:                            ; 'W' <M0 M1 M2 M3>  arm bootloader write
        calls  rx_magic_ok           ; verify 4-byte magic
        JMPR  cc_NE,arm_no
        MOVB  RL4,#0A5h
        MOVB  BLFLAG,RL4
        MOVB  RL4,#06h ; ack
        JMPR  cc_UC,arm_done
arm_no: MOVB  RL4,#0FFh
arm_done: CALL tx
        JMPR  cc_UC,main

; ---- erase: 'E' <a2 a1 a0> -> erase the sector containing that address ----
c_erase:
        calls  rx_word               ; r8/r9 = addr ; RL7 = a2+a1+a0
        calls  rx                    ; received addr-checksum
        CMPB  RL4,RL7                ; verify the erase ADDRESS so a flipped
        JMPR  cc_NE,e_ckbad          ;   addr erased the WRONG sector, e.g. SA3). Reject on mismatch.
        calls  policy_check          ; r8/r9
        CMPB  RL4,#0
        JMPR  cc_NE,e_deny
        calls  amd_sector_erase      ; r8/r9 -> erase (incl. DQ6/DQ5 poll); RL4 = 1/2
        JMPR  cc_UC,e_rep
e_ckbad: MOVB RL4,#04h             ; 4 = addr checksum error -> NO erase performed
        JMPR  cc_UC,e_rep
e_deny: MOVB  RL4,#03h             ; 3 = policy-denied
e_rep:  CALL  tx
        JMPR  cc_UC,main

; ---- The 'P' single-block path is not included. 'C' is the sole program path, keeping the
;      'K'-opcode agent below the SA1-loader CRC-WDT size ceiling (about 1500 B).

c_baud:                             ; 'B' <S0BG>  (1 byte: 9600=77 / 19200=38 / 187500=3)
        calls  rx                    ; RL4 = new S0BG
        calls  asc0_set_bg           ; acks at the OLD baud, then switches
        jmpa  cc_UC,main             ; absolute jump because main is outside relative range

c_reset:
        ; MAGIC-GATED FINALIZE + reset: require 'R' 9C 9C. A stray 'R' (0x52) byte - e.g.
        ; frame data misread as a command after a dropped 'C' opcode - must NOT reset the agent
        ; mid-install (= a blank-sector brick). Non-magic input returns to the command loop.
        calls rx
        cmpb  RL4,#09Ch
        jmpr  cc_NE,c_rstno
        calls rx
        cmpb  RL4,#09Ch
        jmpr  cc_NE,c_rstno
        ; Finalize before reboot while this RAM agent still owns the CPU. E740=0 plus the
        ; stock EEPROM commit at 0x1A62 must not depend on an optional post-reset loader.
        movb  RL4,#0
        movb  0e740h,RL4
        calls 0x1A62
        ; Hybrid reset: shortest WDT is the fallback; protected SRST is the fast path.
        ; Do not remove the other SRVWDT calls: they protect receive/erase/program operations.
        mov   0ffaeh,#0ff00h         ; WDTCON: minimum watchdog period if SRST does not take
        srvwdt
        srst
c_spin: jmpr  cc_UC,c_spin           ; unexpected continuation -> watchdog fallback
c_rstno: jmpa cc_UC,main             ; stray 'R' -> ignore (back to main)

; ============================================================================
; CHUNK FLASH ('C') - loader-style reliability. Receive a 1024-byte block as ONE
; CRC16-checked bulk burst (ONE turnaround per 1KB vs per 128B - turnarounds are where
; the marginal K-line flips), then program it internally in 8x128 sub-blocks. This uses
; the agent-stream loader framing. The host pads every chunk to exactly
; 1024 B (FF tail; the v5 FF-transparent RMW makes pad bytes a no-op), so there is NO
; length field and NO partial tail. CBUF = BUF = 0xE000..0xE3FF.
;   wire : 'C' <a2 a1 a0> <data[1024]> <crcHi crcLo>     (CRC16 big-endian)
;   reply: 1 ok / 2 program-fail / 3 policy-deny / 4 CRC mismatch
; ============================================================================
c_chunk:
        ; --- receive a2/a1/a0, build r8/r9 AND fold each into the CRC DURING the receive. The ~22us
        ;     IRAM fold overlaps each byte's 62us wait and avoids a pause before streamed data. ---
        mov   r12,#0FFFFh            ; CRC16 init
        calls rx                     ; a2
        movbz r9,RL4                 ; r9 = a2
        mov   r4,r9
        calls nibfold                ; fold a2 (overlaps the wait for a1)
        calls rx                     ; a1
        movbz r4,RL4
        mov   r8,r4
        shl   r8,#8                  ; r8 = a1<<8
        calls nibfold                ; fold a1
        calls rx                     ; a0
        movbz r4,RL4
        or    r8,r4                  ; r8 = (a1<<8)|a0
        calls nibfold                ; fold a0 (overlaps the wait for the 1st data byte)
        ; --- receive 1024 data bytes -> CBUF, nibble-folding EACH during rx (fast fold fits the
        ;     inter-byte gap => no overrun; = 1 overlapped pass, not receive-then-fold) ---
        mov   r10,#BUF
        mov   r5,#0400h
cch_rx: cmp   r5,#0
        jmpr  cc_EQ,cch_rxd
        calls  rx                    ; RL4 = byte (rx saves r5; clobbers only RL4 -> r10/r12 survive)
        movb  [r10],RL4              ; store to CBUF
        movbz r4,RL4
        calls  nibfold
        add   r10,#1
        sub   r5,#1
        jmpr  cc_UC,cch_rx
cch_rxd:
        calls  rx                    ; received CRC hi
        movbz r6,RL4
        shl   r6,#8
        calls  rx                    ; received CRC lo
        movbz r4,RL4
        or    r6,r4                  ; r6 = received CRC16 ; r12 already = CRC16(a2,a1,a0,data[0..1023])
        cmp   r12,r6
        jmpr  cc_NE,cch_crcbad
        calls  policy_check          ; start addr (r8/r9) -> RL4 0 ok / 1 deny
        cmpb  RL4,#0
        jmpr  cc_NE,cch_deny
        mov   r6,r8                  ; save start lo16 (r6 free after the CRC compare)
        mov   r7,r9                  ; save start a2
        add   r8,#03FFh              ; end = start + 1023 (last byte of the 1KB chunk)
        addc  r9,#0
        calls  policy_check          ; DUAL-END check: chunk must not span into SA1/top half
        mov   r8,r6                  ; restore start lo16
        mov   r9,r7                  ; restore start a2
        cmpb  RL4,#0
        jmpr  cc_NE,cch_deny
        mov   r10,#BUF               ; src = CBUF
        calls  vpp_on                ; INTEL-28F: raise 12V VPP/RP# for the whole 1KB. program_chunk
        ;                              is chip-agnostic + never touches VPP; without this every word
        ;                              sees VPP<VPPL -> SR.3=1 -> NOTHING programmed. r10/r8/r9 survive
        ;                              vpp_on (clobbers only r5). Erase is bracketed in amd_sector_erase.
        calls  program_chunk         ; -> RL4 = 1 ok / 2 fail
        calls  vpp_off               ; drop 12V after the chunk (data protection between chunks)
        jmpr  cc_UC,cch_rep
cch_crcbad: movb RL4,#04h           ; 4 = CRC mismatch (NOTHING programmed) -> host retries chunk
        jmpr  cc_UC,cch_rep
cch_deny:   movb RL4,#03h           ; 3 = policy-denied
cch_rep:    calls tx
        jmpa  cc_UC,main             ; ABSOLUTE jump (c_chunk is >254 B from main -> JMPR out of range)

; program_chunk - program exactly 1024 B from CBUF (r10) to flash addr r8/r9, in 8 sub-blocks
;   of 128 via the v5 program_amd_core. Loop vars r6(count)/r8/r9(addr)/r10(src) all survive
;   set_target (clobbers r4,r5) + program_amd_core (clobbers r4,r5,r12,r13,r14; push/pops r9).
;   out: RL4 = 1 all ok / 2 program-fail (host retries the whole chunk; AMD re-program idempotent).
program_chunk:
        ; DEFENSE-IN-DEPTH (BRICK-A2-MASK / BRICK-PARAM1-DUALEND): the address is now CRC-
        ; authenticated, so it is always host-aligned & in-range; but guard anyway against a multi-
        ; byte error that survived the CRC. set_target masks a2 to 2 bits, so a2>=4 would alias to a
        ; wrong page; and the 8x0x80 advance must not wrap the 16-bit low word (would alias into a
        ; wrong/param1 sector). Legit chunks have a2<=3 and r8<=0xFC00, so these never trip normally.
        cmp   r9,#4
        jmpr  cc_NC,pch_fail         ; a2 >= 4 -> page-alias risk -> fail (NOTHING programmed)
        cmp   r8,#0fc80h
        jmpr  cc_NC,pch_fail         ; r8 >= 0xFC80 -> last sub-block wraps 0x10000 -> alias -> fail
        mov   r4,#080h
        movb  0e73ah,RL4             ; v5 length = 128 (constant for all 8 sub-blocks; BYTE store)
        mov   r6,#8                  ; sub-block count
pch_lp: cmp   r6,#0
        jmpr  cc_EQ,pch_done
        mov   0e73ch,r10             ; v5 source pointer = current CBUF cursor
        calls  set_target            ; DPP0 + 0xE656 from r8/r9
        calls  program_amd_core      ; programs 128 B; status -> 0xE742
        movb  RL4,0e742h
        cmpb  RL4,#1
        jmpr  cc_NE,pch_fail
        add   r8,#080h               ; 24-bit addr advance (lo16 + carry into a2)
        addc  r9,#0
        add   r10,#080h              ; CBUF cursor += 128
        sub   r6,#1
        jmpr  cc_UC,pch_lp
pch_done: movb RL4,#1
        rets
pch_fail: movb RL4,#2
        rets

; crc16_run - fold r11 bytes from [r5] into the CRC16 accumulator r12 (reflected, poly 0xA001;
;   NO init here - the CALLER inits r12=0xFFFF once, then calls this per region so one CRC can span
;   the address stash + the data. Bit-for-bit == checksum._crc(bytes,0xFFFF); inner loop lifted
;   verbatim, accumulator r6->r12 to keep the chunk addr/CRC regs safe).
;   IN r5 = base, r11 = count, r12 = acc ; OUT r12 ; clobbers r4,r5,r11,r13 ; preserves r6,r7,r8,r9,r10.
build_nibtbl:                       ; fill NIBTBL[0..15] = 4 bit-steps of poly 0xA001 (call once)
        mov   r5,#0
bnt_l:  cmp   r5,#16
        jmpr  cc_EQ,bnt_d
        mov   r4,r5
        mov   r6,#4
bnt_i:  mov   r3,r4
        and   r3,#1
        shr   r4,#1
        cmp   r3,#0
        jmpr  cc_EQ,bnt_x
        xor   r4,#0A001h
bnt_x:  sub   r6,#1
        jmpr  cc_NE,bnt_i
        mov   r3,r5
        shl   r3,#1
        add   r3,#NIBTBL
        mov   [r3],r4
        add   r5,#1
        jmpr  cc_UC,bnt_l
bnt_d:  rets

; nibfold - fold ONE byte (zero-extended in r4) into the CRC r12 via NIBTBL (2 lookups). Clobbers r4,r13.
nibfold:
        xor   r12,r4               ; crc ^= byte
        mov   r13,r12              ; nibble 1
        and   r13,#0Fh
        shl   r13,#1
        add   r13,#NIBTBL
        mov   r13,[r13]
        shr   r12,#4
        xor   r12,r13
        mov   r13,r12              ; nibble 2
        and   r13,#0Fh
        shl   r13,#1
        add   r13,#NIBTBL
        mov   r13,[r13]
        shr   r12,#4
        xor   r12,r13
        rets

; crc16_run - fold r11 bytes from [r5] into r12 via nibfold (bit-identical to host checksum._crc).
;   IN r5=base,r11=count,r12=acc; clobbers r4,r5,r11,r13.
crc16_run:
cqb:    srvwdt
        cmp   r11,#0
        jmpr  cc_EQ,cqd
        movb  RL4,[r5]
        movbz r4,RL4
        calls nibfold
        add   r5,#1
        sub   r11,#1
        jmpr  cc_UC,cqb
cqd:    rets

; ============================================================================
; CRC-CHECKED READ ('K') - the high-baud reliable READ (mirror of the 'C' chunk write).
; Reads n bytes (1..1024) into CBUF, folds a2/a1/a0 + the n data bytes through the SAME
; crc16 as the write path (init 0xFFFF, poly 0xA001, reflected == host checksum._crc), then
; streams the n bytes followed by the CRC16 (big-endian). The host recomputes the CRC over the
; address it SENT + the data it RECEIVED and re-requests on mismatch -> integrity WITHOUT a
; second read pass. A flipped command/address/data byte fails the CRC = caught, not
; silently returned. Reads never brick (no erase/program), so NO policy_check here.
;   wire : 'K' <a2 a1 a0> <nHi nLo>
;   reply: <data[n]> <crcHi crcLo>            (CRC16 == host _crc(a3+data,0xFFFF), big-endian)
; Regs: r7 = n (PRESERVED across crc16_run, which saves r6/r7); r8/r9 = addr; r10 = flash read
;   cursor; r11 = CBUF write cursor; r12 = CRC acc; r5 = working counter. rx/tx clobber only RL4
;   (+ rx saves r5); set_target clobbers r4,r5; crc16_run clobbers r4,r5,r11,r13.
; ============================================================================
c_crcread:
        calls  rx_word               ; r8/r9 = addr (a2 in r9 ; (a1<<8)|a0 in r8). r7 set AFTER
        ;                              (rx_word uses RL7 as an addr-cksum scratch = would clobber r7).
        calls  rx                    ; n high byte
        movbz  r7,RL4
        shl    r7,#8
        calls  rx                    ; n low byte
        movbz  r4,RL4
        or     r7,r4                 ; r7 = n
        cmp    r7,#0400h
        jmpr   cc_ULE,crd_cap        ; n <= 1024: ok
        mov    r7,#0400h             ; CAP at CBUF size: a corrupted length must not overflow RAM
crd_cap:
        ; --- stash a2,a1,a0 at ACRC for the CRC fold (identical byte layout to c_chunk) ---
        mov    r4,r9
        movb   0e404h,RL4            ; ACRC+0 = a2  (r9 low byte)
        mov    r4,r8
        shr    r4,#8
        movb   0e405h,RL4            ; ACRC+1 = a1  (r8 high byte)
        mov    r4,r8
        movb   0e406h,RL4            ; ACRC+2 = a0  (r8 low byte)
        ; --- init CRC16 (0xFFFF) + fold the 3 address bytes FIRST (== host _crc(a3+data)) ---
        mov    r12,#0FFFFh
        mov    r5,#0e404h            ; ACRC base (a2,a1,a0)
        mov    r11,#3
        calls  crc16_run             ; r12 = CRC16(a2,a1,a0); preserves r6,r7,r8,r9,r10,r12
        ; --- set up the flash read cursor (DPP0 page-walk); set_target clobbers ONLY r4,r5 (r12 safe) ---
        calls  set_target            ; DPP0(page) + 0xE656(offset) from r8/r9
        mov    r10,0e656h            ; r10 = in-page CPU read cursor
        ; --- PIPELINED read+fold+tx: the CRC fold and next-byte read overlap the current byte's
        ;     ~62us shift-out, keeping the loop near wire rate. ---
        bclr   0ffb0h.4              ; S0REN=0 for the whole stream (our tx echo can't loop back)
        mov    r5,r7                 ; r5 = remaining = n  (SET AFTER set_target clobbers r5)
        cmp    r5,#0
        jmpr   cc_EQ,crd_ct          ; n==0 -> just send the CRC trailer
        movb   RL6,[r10]             ; PRIME: read the first byte (RL6 = byte to send)
crd_pl: movb   0feb0h,RL6            ; S0TBUF = current byte -> START its transmit
        movbz  r4,RL6                ; --- nibble-table fold of the current byte (overlaps the tx) ---
        calls  nibfold
        add    r10,#1                ; --- advance flash cursor (+ DPP0 page-walk) ---
        cmp    r10,#04000h
        jmpr   cc_C,crd_nw
        add    0fe00h,#1             ; DPP0++ (next 16KB CPU page)
        mov    r10,#0
crd_nw: sub    r5,#1                 ; remaining--
        jmpr   cc_EQ,crd_pw          ; last byte -> do NOT read past the region
        movb   RL6,[r10]             ; read the NEXT byte (overlaps the current transmit)
crd_pw: srvwdt                       ; --- wait current byte fully shifted out (WDT-serviced) ---
        jnb    0ff6ch.7,crd_pw
        bclr   0ff6ch.7
        cmp    r5,#0
        jmpr   cc_NE,crd_pl          ; more bytes -> send next (RL6 holds it)
crd_ct: bset   0ffb0h.4              ; S0REN=1 -> re-arm receiver before the CRC trailer / next cmd
crd_td:
        ; --- stream CRC16 big-endian (hi then lo). tx sends RL4; r12 survives tx. ---
        mov    r4,r12
        shr    r4,#8
        calls  tx                   ; CRC hi
        mov    r4,r12
        calls  tx                   ; CRC lo (RL4 = r12 low byte)
        jmpa   cc_UC,main           ; far from main -> absolute jump

; ============================================================================
; AMD flash primitives - thin wrappers over the v5 cores.
; program_amd_core / erase_amd_core = the bodies of program_amd.asm and
; erase_amd.asm, lifted VERBATIM and assembled in as subroutines (entry..rets).
; Position-independent (relative jmpr + absolute SFR/SRAM refs) -> relocate into
; the agent image unchanged. DO NOT re-derive them.
;
; I/O CONTRACT (cores read/write these SRAM vars; the agent owns all RAM):
;   0xE656 = target word addr      0xE73C = source-buffer pointer
;   0xE73A = byte length           0xE744 = erase coarse-timeout
;   0xE742 = result 1 ok / 2 fail  0xE741 = program progress (+=2/word)
;   DPP0(0xFE00) must select the flash-bank page so the AA/55 unlock at off
;   0xAAA/0x554 hits the chip command register and [target] hits the sector.
;   set_target implements the firmware orchestrator's DPP0 page/offset split.
;   The cores advance DPP0 on 16KB crossings.
; Call convention: the v5 cores end in `rets`, so reach them with `calls`
;   (same-segment ok) — or change their final `rets`->`ret` and use `callr`.
; ============================================================================


; ============================================================================
; INTEL 28F200 flash primitives -- replace the AMD cores.  I/O CONTRACT identical
; to the AMD cores so the chip-agnostic protocol code above is UNTOUCHED:
;   0xE656 = target word addr   0xE73C = source-buffer pointer   0xE73A = byte length
;   0xE742 = result 1 ok / 2 fail   0xE744 = erase coarse-timeout   0xE741 = progress(+=2)
;   DPP0 (0xFE00) selects the flash-bank page (set by set_target).
; Command set (28F200BX-B, matching monitor_flash.asm):
;   erase   : 0x20 -> block ; 0xD0 -> block (confirm) ; poll SR (0x70/read) SR.7=ready,
;             SR.5=erase-err, SR.3=VPP-low ; 0xFF -> read-array.
;   program : 0x40 -> WA ; data -> WA ; poll SR SR.7=ready, SR.4=prog-err, SR.3=VPP ; 0xFF.
; VPP: the ECU switches 12V through P2.6 + P3.6 (ST90E30
;   "flashing" signal).  Both bset (as outputs) BEFORE the op, bclr AFTER.  This is the
;   firmware's own flash bracket (monitor_flash.asm) -- NO external/host 12V step needed.
;   The 29F is single-supply so its agent skips this; the 28F MUST do it or every write
;   silently fails (VPP<VPPL -> SR.3=1, contents unaltered).
; Writes to the CUI/array are WORD (mov [Rw],Rw) so WE# strobes on this 16-bit bus; SR
;   reads are BYTE (movb) -- SR is on DQ0-7.  For Intel the command ADDRESS is a don't-care
;   (any write latches the CUI), so we write the command to the TARGET word addr (r12/[0xE656]).
; ============================================================================

; --- vpp_on / vpp_off: gate the 12V VPP+RP# rail via P2.6 (+ P3.6). ---
;   P3.6 uses BIT ops so the P3.10/P3.11 ASC0 serial pins are PRESERVED (untouched).
;   DPx.6=1 = output; P2.6/P3.6 latch=1 = drive the switch on.  (Manual 10.x port model.)
;   SFRs (verified vs 80C166 datasheet): P2=0xFFC0 DP2=0xFFC2 P3=0xFFC4 DP3=0xFFC6 (all bit-addr).
vpp_on:
        bset  0ffc2h.6                ; DP2.6 = 1 (P2.6 output)
        bset  0ffc0h.6                ; P2.6  = 1 (12V switch ON)
        bset  0ffc6h.6                ; DP3.6 = 1 (P3.6 output)
        bset  0ffc4h.6                ; P3.6  = 1 (ST90E30 flashing signal)
        mov   r5,#02000h             ; settle: let the 12V rail rise before the first CUI write
vpon_d: srvwdt
        sub   r5,#1
        jmpr  cc_NE,vpon_d
        rets
vpp_off:
        bclr  0ffc0h.6                ; P2.6 = 0 (12V switch OFF -> VPP/RP# back to read level)
        bclr  0ffc4h.6                ; P3.6 = 0
        rets

; --- amd_sector_erase: kept NAME (caller in c_erase unchanged); Intel body. ---
;   in: r8 = addr lo16, r9 = addr hi byte(a2).  set_target -> DPP0 + 0xE656 (target).
amd_sector_erase:
        calls set_target             ; DPP0(page) + 0xE656(offset) from the address
        mov   r4,#0400h              ; initialize the erase coarse-timeout ceiling
        mov   0e744h,r4              ;   0xE744 (Intel block-erase ~1.5-3s typ / ~18s max vs AMD ~1s; AMD seeded
                                     ;   0x40 here). Without this, stale RAM trips a premature timeout -> IEFAIL
                                     ;   -> vpp_off mid-erase -> VPP-abort -> partial erase. SR.7 exit normally wins.
        calls vpp_on                 ; raise 12V VPP/RP# (unlocks ALL blocks incl. boot)
        calls intel_erase_core       ; 0x20/0xD0 -> block; SR poll; status -> 0xE742
        calls vpp_off                ; drop 12V (data protection between ops)
        movb  RL4,0e742h             ; RL4 = 1 ok / 2 fail
        rets

; keep set_target here (chip-agnostic addressing) --------------------------------
; --- set_target: addressing matches the firmware orchestrator at file 0x5078-0x50F8,
;     which does `mov 0xfe00,#page` + `mov 0xe656,r5`). Full 24-bit CPU/DS2 addr =
;     (a2<<16)|r8. The host descrambles (like ds2.py); the GAL maps CPU->chip; the
;     cockpit switch sets A17(bank). DPP0 = addr>>14 ; 0xE656 = addr & 0x3FFF.
;     The v5 cores advance DPP0 on 16KB crossings; the AA/55 unlock at off 0xAAA/0x554
;     hits the chip command reg regardless of DPP0 (only the low addr bits decode it).
set_target:                  ; in: r8 = lo16, r9 = hi byte(a2, 0..3 for 256KB)
        mov   r4,r8
        shr   r4,#14                  ; r8>>14  (page low 2 bits)
        mov   r5,r9
        and   r5,#3                   ; mask a2 to 2 bits (match fw orchestrator
                                      ;   DPP0=(a2&3)<<2|... ; immune to an out-of-range a2)
        shl   r5,#2                   ; a2<<2   (page high bits)
        or    r4,r5
        mov   0fe00h,r4               ; DPP0 = page
        mov   r4,r8
        and   r4,#3fffh
        mov   0e656h,r4               ; 0xE656 = in-page offset
        rets

; --- policy is ADDRESS-based now: bootloader = CPU addr < 0x2000 (SA1/param1) ---
;     top half -> deny all writes; bottom -> deny addr<0x2000 unless 'W'-armed.
;     (replaces the sector#-table version; r8/r9 = the target address.)

; ============================================================================
; COMMS — polled ASC0 over the K-line. ECHO HANDLING matches the STOCK fw: the
; single-wire K-line loops our TX back onto RX, and the fw transmit path DISABLES
; the receiver for the whole response (`bclr S0CON.4` = S0REN @file 0x56A6) instead
; of discarding echoes. We do the same: `tx` clears S0REN before sending; the next
; `rx` re-enables it + flushes. No echo-discard, no timing race. rx SRVWDTs idle.
; SFRs: S0CON 0xFFB0, S0BG 0xFEB4, S0TBUF 0xFEB0, S0RBUF 0xFEB2,
;       S0RIC 0xFF6E (RX-ready=bit7=S0RIR), S0TIC 0xFF6C (TX-done=bit7=S0TIR).
; Format: 8E2 (S0CON=0x80E7 = the stock DS2 framing). The host stays 8E2 for the whole session
;   with no mid-session parity switch. To use the silicon-BSL app-note's 8N1 framing instead,
;   set 0x8011 here and add the matching 8E2->8N1 switch in softbsl_host.py.
; ============================================================================
asc0_set_bg:                          ; in: RL4 = new S0BG; mirror the stock baud-switch
;   (d_ph.asm 0x393C0 hot-switch + 0x3938A flush), not a bare `mov S0BG`. A bare write can leave
;   S0R=1 during reload and retain stale RX/error state. Ack at the current baud, gate S0R,
;   write S0BG, clear S0CON error flags + drain S0RBUF +
;   clear RX/ERR pending, restart S0R + S0REN. (S0CON.15/.13/.4 ARE bit-addressable — fw uses fed8/ffd8.)
        movbz r5,RL4                  ; r5 = new S0BG (save before the ack clobbers RL4)
        movb  RL4,#06h
        calls tx                       ; ACK at the OLD (still-matched) rate; tx waits S0TIR = ack out
        bclr  0ffb0h.15               ; S0R=0 -> BRG stopped, the S0BG reload is DEFERRED (no glitch)
        mov   0feb4h,r5               ; S0BG = new divisor (written while stopped)
        bclr  0ffb0h.8                ; clear S0CON error flags S0PE/S0FE/S0OE (== stock bfldh #0x7,#0)
        bclr  0ffb0h.9                ;   -> un-latch any transition framing/overrun error so RX resumes
        bclr  0ffb0h.10
        movb  RL4,0feb2h              ; drain stale S0RBUF (stock double-reads via 0xFF1C alias)
        movb  RL4,0feb2h
        bclr  0ff6eh.7                ; S0RIC.7 (S0RIR) = 0  clear RX pending
        bclr  0ff70h.7                ; S0EIC.7 (S0EIR) = 0  clear ERROR pending
        bset  0ffb0h.15               ; S0R=1 -> deferred reload fires cleanly at a cycle boundary
        bset  0ffb0h.4                ; S0REN=1 -> re-arm the receiver for the next host byte
        rets

rx_block:                             ; out: RL4 = byte. BLOCKING (waits indefinitely). Used ONLY
        bset  0ffb0h.4                ; by main's command-wait: the agent must idle until the host
        bclr  0ff6eh.7                ; sends the next command. S0REN=1 ; clear any stale S0RIR.
rxb_w:  srvwdt                        ; keep WDT alive while waiting for the host
        jnb   0ff6eh.7,rxb_w          ; S0RIR?
        movb  RL4,0feb2h              ; S0RBUF
        bclr  0ff6eh.7
        rets

; rx - bounded receive. Identical to rx_block but gives up after ~0xFFFF spins
;   (~100ms @12MHz; WDT serviced throughout; << host 8s status wait; >> the ~1.25ms inter-byte gap
;   in a 9600 burst, so it never false-times-out mid-burst). On timeout returns sentinel 0 so a
;   DROPPED frame byte makes the frame DESYNC -> CRC/cksum FAIL -> status 4 -> host retries, instead
;   of HANGING the agent mid-frame (which after an erase = a blank-sector brick). Used for ALL
;   in-frame reads (rx_word, chunk receive + CRC, erase cksum, ...). Still clobbers ONLY RL4
;   (r5 is saved/restored on the system stack).
rx:
        bset  0ffb0h.4                ; S0REN=1 (re-enable RX after our last TX)
        bclr  0ff6eh.7                ; clear any stale S0RIR
        push  r5
        mov   r5,#0ffffh              ; spin budget
rx_w:   srvwdt                        ; keep WDT alive while waiting
        jnb   0ff6eh.7,rx_chk         ; not ready -> count down
        movb  RL4,0feb2h              ; S0RBUF
        bclr  0ff6eh.7
        pop   r5
        rets
rx_chk: sub   r5,#1
        jmpr  cc_NE,rx_w              ; budget left -> keep waiting
        pop   r5                      ; TIMEOUT: give up -> sentinel -> frame desyncs -> CRC fail
        movb  RL4,#0
        rets

tx:                                   ; in: RL4 = byte. STOCK echo handling: drop the
        bclr  0ffb0h.4                ; receiver while transmitting (S0REN=0) so our own
        movb  0feb0h,RL4              ; byte can't loop into RX. (next rx re-enables it.)
tx_wt:  srvwdt
        jnb   0ff6ch.7,tx_wt          ; wait S0TIR (byte fully shifted out)
        bclr  0ff6ch.7
        rets

debounce:                             ; ~few ms after a mechanical switch flip
        mov   r4,#0ffffh
db_l:   srvwdt
        sub   r4,#1
        jmpr  cc_NE,db_l
        rets

; ---- rx_word: read a2 a1 a0 -> r8=(a1<<8)|a0, r9=a2 ----
rx_word:
        movb  RL7,#0                  ; addr checksum accumulator (a2+a1+a0), checked by c_erase
        calls  rx
        addb  RL7,RL4
        movbz r9,RL4                  ; a2
        calls  rx
        addb  RL7,RL4
        movbz r8,RL4
        shl   r8,#8                   ; a1<<8
        calls  rx
        addb  RL7,RL4
        movbz r4,RL4
        or    r8,r4                   ; |a0
        rets

; rx_addr_len_data was retired with the 'P' path (its only caller). The 'C' chunk uses its own
; CRC16-authenticated receive (c_chunk); rx_word still handles the 'E'/'K' address reads.

; ---- rx_magic_ok: 'W' arm — read 4 magic bytes, RL4=0 if == "SBSL" else !=0 ----
rx_magic_ok:
        calls rx
        cmpb RL4,#053h ; 'S'
        jmpr cc_NE,rmx
        calls rx
        cmpb RL4,#042h ; 'B'
        jmpr cc_NE,rmx
        calls rx
        cmpb RL4,#053h ; 'S'
        jmpr cc_NE,rmx
        calls rx
        cmpb RL4,#04Ch ; 'L'
        rets                           ; cc set from last cmpb: EQ => ok
rmx:    ret                           ; NE


; ---- identify: read the bank-ID marker -> MARKER/HALF; return marker byte ----
; marker @ file 0x5FFC = CPU 0x1FFC: A5 5A <half> <~half> ; FF*4 = blank/uninit.
; HALF records the recognized bank marker for identify/reporting. It no longer controls
; erase/program permission because this agent executes from RAM on either visible half.
identify:
        mov   0fe00h,#0               ; DPP0 = 0 so [0x1FFC] hits the lows window
        mov   r6,#01ffch              ; marker base ptr in r6 (RL6/RH6 unused
                                      ;   here) so the RL4 byte scratch can't clobber the
                                      ;   pointer because r4 and RL4 share the same word register.
        movb  RL5,[r6+#2]             ; cache the half byte for the 'I' reply
        movb  MARKER,RL5
        movb  RL4,#1                  ; default = PROTECTED
        movb  HALF,RL4
        ; --- allowance 1: all four marker bytes 0xFF -> blank/fresh install -> bottom ---
        movb  RL4,[r6]
        cmpb  RL4,#0FFh
        jmpr  cc_NE,id_sig
        movb  RL4,[r6+#1]
        cmpb  RL4,#0FFh
        jmpr  cc_NE,id_sig
        movb  RL4,[r6+#2]
        cmpb  RL4,#0FFh
        jmpr  cc_NE,id_sig
        movb  RL4,[r6+#3]
        cmpb  RL4,#0FFh
        jmpr  cc_NE,id_sig
        movb  RL4,#0                  ; blank -> bottom/writable
        movb  HALF,RL4
        jmpr  cc_UC,id_d
id_sig: ; --- allowance 2: A5 5A signature + exact complement + half byte == 'B' (0x42) ---
        movb  RL4,[r6]
        cmpb  RL4,#0A5h
        jmpr  cc_NE,id_d              ; bad signature -> stay PROTECTED
        movb  RL4,[r6+#1]
        cmpb  RL4,#05Ah
        jmpr  cc_NE,id_d
        movb  RL4,[r6+#2]            ; half
        movb  RL5,[r6+#3]            ; ~half
        xorb  RL4,RL5
        cmpb  RL4,#0FFh              ; complement must be exact
        jmpr  cc_NE,id_d             ; bad complement -> PROTECTED
        movb  RL4,[r6+#2]
        cmpb  RL4,#042h ; 'B'
        jmpr  cc_NE,id_d             ; 'T'/garbage -> PROTECTED (golden-safe)
        movb  RL4,#0                 ; valid 'B' -> bottom/writable
        movb  HALF,RL4
id_d:   movb  RL4,MARKER
        rets
; ===== INTEL erase core (replaces erase_amd_core; SAME name kept for callers) =====
; Erases ONE 28F200 block containing [0xE656].  VPP already ON (caller brackets it).
; Status: 0xE742 = 1 ok / 2 fail.  WDT serviced in the poll (erase ~1.5-3 s).
; Clobbers r4,r5,r12,r13,r14 (matches the AMD core's clobber set; r0/r9 safe).
erase_amd_core:
intel_erase_core:
        mov   r12,0xfe52             ; r12 = T1 snapshot (coarse-timeout timing, like the AMD core)
        mov   r14,#0x0               ; r14 = coarse-timeout counter
        mov   r4,0xe656              ; r4 = target (block) word addr
        ; ---- Intel two-cycle block erase: 0x20 (setup) then 0xD0 (confirm) ----
        mov   r5,#0x0020
        mov   [r4],r5                ; 0x20 -> block  (Erase Setup)
        mov   r5,#0x00D0
        mov   [r4],r5                ; 0xD0 -> block  (Erase Confirm -> WSM starts)
        ; ---- poll the Status Register: 0x70 then read; SR.7=ready, SR.5=erase-err, SR.3=VPP ----
IEPOLL:
        mov   0ffaeh,#0x101          ; WDTCON reload (mirror the AMD erase core)
        srvwdt                       ; keep the inherited WDT alive across the ~seconds erase
        mov   r5,0xfe52              ; coarse-timeout bookkeeping (backstops a dead WSM)
        sub   r5,r12
        cmp   r5,#0x754              ; ~1876 T1 ticks elapsed?
        jmpr  cc_C,IERD
        add   r14,#0x1
        mov   r12,0xfe52
IERD:
        mov   r4,0xe656
        mov   r5,#0x0070
        mov   [r4],r5                ; 0x70 -> Read Status Register
        movb  RL5,[r4]               ; SR (low byte on DQ0-7)
        andb  RL5,#0x80              ; SR.7 (WSM status): 1 = ready
        jmpr  cc_NE,IEDONE           ; WSM ready -> check pass/fail bits
        cmp   r14,0xe744             ; coarse timeout reached?
        jmpr  cc_C,IEPOLL            ; r14 < [0xE744] -> keep polling
        jmpr  cc_UC,IEFAIL           ; timed out with WSM never ready -> fail
IEDONE:
        ; SR.7=1: interrogate error bits SR.5 (erase) and SR.3 (VPP low)
        mov   r4,0xe656
        mov   r5,#0x0070
        mov   [r4],r5
        movb  RL5,[r4]
        andb  RL5,#0x28              ; SR.5 (0x20 erase-err) | SR.3 (0x08 VPP-low)
        jmpr  cc_NE,IEFAIL
        movb  RL5,#0x1
        movb  0e742h,RL5             ; status = 1 (success)
        jmpr  cc_UC,IERST
IEFAIL:
        movb  RL5,#0x2
        movb  0e742h,RL5             ; status = 2 (fail)  [host _decode_sr]
        mov   r4,0xe656
        mov   r5,#0x0050
        mov   [r4],r5                ; 0x50 -> Clear Status Register (un-latch the error bits)
IERST:
        mov   r4,0xe656
        mov   r5,#0x00FF
        mov   [r4],r5                ; 0xFF -> Read Array (back to normal read mode)
        rets

; ===== INTEL program core (replaces program_amd_core; SAME name kept for callers) =====
; Programs [0xE73A] BYTES (== 128 for the chunk path) as WORDS from [0xE73C] to the word
; addr [0xE656].  DPP0 advances on 16KB page crossings (like the AMD core).  VPP already ON
; (c_chunk brackets each 1KB with vpp_on/vpp_off around 'calls program_chunk').
; Intel programming only clears 1->0, so a 0xFFFF word is a true no-op (never errors) -> SKIP
;   FF words (faster, no spurious SR).  Status 0xE742 = 1 ok / 2 fail; progress += 2 per word.
; Clobbers r4,r5,r12,r13,r14; pushes/pops r9 (preserves the DPP0 save, like the AMD core).
program_amd_core:
intel_program_core:
        mov   [-r0],r9              ; push r9
        mov   r9,0xfe00            ; save DPP0
        mov   r12,0xe656          ; r12 = target word addr
        mov   r14,#0x0           ; r14 = byte index
        movb  RL4,#0x1           ; initialize status=1 once at entry; the per-word path
        movb  0e742h,RL4         ;   only ever writes 2 (on error) -> 0xE742 is truly STICKY (a later ok/FF
                                 ;   word can no longer clear a prior word's failure). Host read-back = gate.
        jmpr  cc_UC,IPTEST
IPBODY:
        ; ---- build the data word: low = src[i], high = src[i+1] ----
        mov   r4,r14
        movbz r4,RL4
        mov   r5,0xe73c
        add   r5,r4
        movb  RL4,[r5]            ; R8-R15 have no byte halves (RL13/RH13 invalid), so build the
        add   r5,#1              ;   word in r4 (has RL4/RH4) then copy. r4 free here (index already in r5). RL4=data low
        movb  RH4,[r5]           ;   RH4 = data high -> r4 = the 16-bit data word (little-endian)
        mov   r13,r4             ;   r13 = the data word (for the 0xFFFF-skip check + the CUI data write)
        ; ---- FF-transparent: a 0xFFFF word programs nothing on Intel -> skip it ----
        cmp   r13,#0xFFFF
        jmpr  cc_EQ,IPADV         ; all-ones word advances without clearing sticky status
        ; ---- Intel word write: 0x40 (setup) then data -> target (WSM latches on this write) ----
        mov   r5,#0x0040
        mov   [r12],r5           ; 0x40 -> WA  (Program Setup)
        mov   [r12],r13          ; data word -> WA (WSM starts; r13 free to reuse now)
        ; ---- poll SR: the CUI automatically exposes status after the data write.
        ; Read once per poll and retain that same sample for the ready/error checks;
        ; issuing 0x70 before every read and then re-reading a ready status only adds
        ; flash-bus cycles.  Each movb read supplies the OE#/CE# toggle required to
        ; refresh SR.  SR.7=ready, SR.4=program error, SR.3=VPP low. ----
        mov   r13,#0xffff        ; per-word poll guard (data already latched above)
IPPOLL:
        mov   0ffaeh,#0x101
        srvwdt
        movb  RL5,[r12]          ; SR (program operation enters status-read mode automatically)
        movb  RL4,RL5            ; preserve the complete sample for IPRDY
        nop                       ; layout-stability padding: keep all following agent entry/branch addresses fixed
        nop
        andb  RL4,#0x80          ; SR.7 ready?
        jmpr  cc_NE,IPRDY
        sub   r13,#1
        jmpr  cc_NE,IPPOLL
        jmpr  cc_UC,IPERR        ; guard exhausted, never ready -> error
IPRDY:
        movb  RL4,RL5            ; use the ready sample; no second status transaction needed
        nop                       ; layout-stability padding for the status-read sequence
        nop
        nop
        andb  RL4,#0x18          ; SR.4 (0x10 prog-err) | SR.3 (0x08 VPP-low)
        jmpr  cc_NE,IPERR
        movb  RL5,#0x2
        addb  0e741h,RL5         ; word ok: progress += 2
        jmpr  cc_UC,IPADV        ; success advances without clearing sticky failure state
IPERR:
        mov   r5,#0x0050
        mov   [r12],r5           ; Clear Status Register (un-latch prog/VPP error)
        movb  RL4,#0x2
        movb  0e742h,RL4         ; status = 2 (err) -- STICKY (init=1 at entry; per-word path never re-writes 1)
        ; fall through without clearing sticky failure status
IPADV:
        ; ---- advance target word; DPP0++ on 16KB page crossing ----
        mov   r4,#0x3ffe
        add   r12,#0x2
        mov   r5,r12
        cmp   r5,r4
        jmpr  cc_ULE,IPNEXT
        add   0xfe00,#0x1        ; DPP0 -> next page
        mov   r12,#0x0
IPNEXT:
        mov   r4,r14
        addb  RL4,#0x2           ; index -> next WORD (+2 bytes)
        mov   r14,r4
IPTEST:
        mov   r4,r14
        cmpb  RL4,0xe73a         ; index < length?
        jmpr  cc_C,IPBODY
        ; ---- exit: read-array, restore DPP0, pop r9 ----
        mov   r12,0xe656         ; re-point at the start-page target for the FF write
        mov   r5,#0x00FF
        mov   [r12],r5           ; 0xFF -> Read Array
        mov   0xfe00,r9          ; restore DPP0
        mov   r9,[r0+]           ; pop r9
        rets
