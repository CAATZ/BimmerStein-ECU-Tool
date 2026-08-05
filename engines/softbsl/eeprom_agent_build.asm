; eeprom_agent_build.asm - temporary MS41 24C04 service.
; Loads at 0xD800 through the existing CRC-checked Soft-BSL 0x5A loader.
;
; This agent deliberately contains no flash erase/program command.  It supports:
;   'i'                         -> version, capabilities, entry E740
;   'd'                         -> status + 512 physical bytes + CRC16
;   'w' addr[2] old new crc[2] -> replay-safe compare/write/readback of one byte
;   'q' C3 3C                  -> protected SRST; normalize E740 only from mode 1
;   'R' 9C 9C                  -> shared Soft-BSL failure-cleanup alias
;
; The physical read/write path is a self-contained, stock-derived P3.3/P3.12
; bit-banged 24C04 implementation.  It selects A8 in the control byte, so every
; address from 0x000 through 0x1FF is reachable without firmware entry points.
; Writes use one EEPROM byte per transaction; this is deliberately compact and
; cannot wrap either a 16-byte page or the 0x100 bank boundary.
;
; Field policy, additive checks, backups, ordering, and whole-image verification
; remain host-owned.  The agent accepts only one replay-safe byte at a time.
base 0d800h

BUF0       EQU 0E000h
BUF0HI     EQU 0E100h
BUF1       EQU 0E200h
BUF1HI     EQU 0E300h
ENTRYMODE  EQU 0E400h
WRITEADDR  EQU 0E402h
EXPECTED   EQU 0E404h
NEWBYTE    EQU 0E405h
NIBTBL     EQU 0FD60h

agent_entry:
        bclr  PSW.11
        mov   SP,#0FC00h
        mov   CP,#0FA00h
        mov   r0,#0E600h
        mov   DPP0,#4
        mov   DPP1,#5
        mov   DPP2,#0
        mov   DPP3,#3
        bset  0FFC4h.3
        bset  0FFC6h.3
        bclr  0FFC6h.12
        movb  RL4,0E740h
        movb  ENTRYMODE,RL4
        movb  RL4,#0A5h
        calls tx
        calls build_nibtbl

main:
        srvwdt
        calls rx_block
        cmpb  RL4,#'i'
        jmpr  cc_NE,main_d
        jmpa  cc_UC,c_ident
main_d: cmpb  RL4,#'d'
        jmpr  cc_NE,main_p
        jmpa  cc_UC,c_dump
main_p: cmpb  RL4,#'w'
        jmpr  cc_NE,main_q
        jmpa  cc_UC,c_write
main_q: cmpb  RL4,#'q'
        jmpr  cc_NE,main_R
        jmpa  cc_UC,c_quit
main_R: cmpb  RL4,#'R'
        jmpr  cc_NE,c_nak
        jmpa  cc_UC,c_recover
c_nak: movb  RL4,#0FFh
        calls tx
        jmpa  cc_UC,main

; version 3, caps bit0=full read / bit1=replay-safe generic byte writer /
; bit2=self-contained full-address 24C04 I/O /
; bit3=conditional E740 finalizer, entry E740.
c_ident:
        movb  RL4,#3
        calls tx
        movb  RL4,#0Fh
        calls tx
        movb  RL4,ENTRYMODE
        calls tx
        jmpa  cc_UC,main

; Physical full-device read.  Each pass starts with a different fill pattern;
; an early I2C abort therefore cannot masquerade as matching data.
c_dump:
        mov   r5,#BUF0
        mov   r6,#0200h
        movb  RL4,#0A5h
        calls fill_bytes
        mov   r5,#BUF1
        mov   r6,#0200h
        movb  RL4,#05Ah
        calls fill_bytes

        ; Split at the physical 24C04 bank boundary and service the watchdog
        ; between banks.
        mov   r12,#BUF0
        mov   r13,#0
        mov   r14,#0100h
        calls ee_read
        srvwdt
        mov   r12,#BUF0HI
        mov   r13,#0100h
        mov   r14,#0100h
        calls ee_read
        srvwdt
        mov   r12,#BUF1
        mov   r13,#0
        mov   r14,#0100h
        calls ee_read
        srvwdt
        mov   r12,#BUF1HI
        mov   r13,#0100h
        mov   r14,#0100h
        calls ee_read
        srvwdt

        mov   r5,#BUF0
        mov   r6,#BUF1
        mov   r7,#0200h
dump_cmp:
        movb  RL4,[r5]
        movb  RL3,[r6]
        cmpb  RL4,RL3
        jmpr  cc_NE,dump_bad
        add   r5,#1
        add   r6,#1
        sub   r7,#1
        jmpr  cc_NE,dump_cmp

        ; CRC16 covers the positive status byte plus all 512 bytes.
        mov   r12,#0FFFFh
        mov   r4,#1
        calls nibfold
        mov   r5,#BUF0
        mov   r11,#0200h
        calls crc16_run
        movb  RL4,#1
        calls tx
        mov   r5,#BUF0
        mov   r11,#0200h
dump_tx:
        movb  RL4,[r5]
        calls tx
        add   r5,#1
        sub   r11,#1
        jmpr  cc_NE,dump_tx
        calls tx_crc
        jmpa  cc_UC,main
dump_bad:
        movb  RL4,#2
        calls tx
        jmpa  cc_UC,main

; Replay-safe generic byte write. CRC16 covers command, big-endian address,
; expected byte, and replacement byte. Replaying the exact frame after a lost
; reply is safe: an already-replaced byte returns success without another write.
c_write:
        calls rx
        movbz r13,RL4
        shl   r13,#8
        calls rx
        movbz r4,RL4
        or    r13,r4
        mov   WRITEADDR,r13
        calls rx
        movb  EXPECTED,RL4
        calls rx
        movb  NEWBYTE,RL4
        calls rx
        movbz r6,RL4
        shl   r6,#8
        calls rx
        movbz r4,RL4
        or    r6,r4

        mov   r13,WRITEADDR
        cmp   r13,#0200h
        jmpr  cc_NC,write_deny

        mov   r12,#0FFFFh
        mov   r4,#'w'
        calls nibfold
        mov   r4,WRITEADDR
        shr   r4,#8
        and   r4,#0FFh
        calls nibfold
        mov   r4,WRITEADDR
        and   r4,#0FFh
        calls nibfold
        movbz r4,EXPECTED
        calls nibfold
        movbz r4,NEWBYTE
        calls nibfold
        cmp   r12,r6
        jmpr  cc_NE,write_crc_bad

        ; Two successful physical reads must agree before comparison.
        movb  RL4,#0A5h
        movb  BUF0,RL4
        movb  RL4,#05Ah
        movb  BUF1,RL4
        mov   r12,#BUF0
        mov   r13,WRITEADDR
        mov   r14,#1
        calls ee_read
        cmp   r4,#0
        jmpr  cc_NE,write_read_bad
        mov   r12,#BUF1
        mov   r13,WRITEADDR
        mov   r14,#1
        calls ee_read
        cmp   r4,#0
        jmpr  cc_NE,write_read_bad
        movb  RL4,BUF0
        cmpb  RL4,BUF1
        jmpr  cc_NE,write_read_bad
        cmpb  RL4,NEWBYTE
        jmpr  cc_EQ,write_ok
        cmpb  RL4,EXPECTED
        jmpr  cc_NE,write_stale

        mov   r12,#NEWBYTE
        mov   r13,WRITEADDR
        mov   r14,#1
        calls ee_write
        cmp   r4,#0
        jmpr  cc_NE,write_failed

        ; Two successful readbacks must both contain the replacement.
        movb  RL4,#0A5h
        movb  BUF0,RL4
        movb  RL4,#05Ah
        movb  BUF1,RL4
        mov   r12,#BUF0
        mov   r13,WRITEADDR
        mov   r14,#1
        calls ee_read
        cmp   r4,#0
        jmpr  cc_NE,write_failed
        mov   r12,#BUF1
        mov   r13,WRITEADDR
        mov   r14,#1
        calls ee_read
        cmp   r4,#0
        jmpr  cc_NE,write_failed
        movb  RL4,BUF0
        cmpb  RL4,BUF1
        jmpr  cc_NE,write_failed
        cmpb  RL4,NEWBYTE
        jmpr  cc_NE,write_failed
write_ok:
        movb  RL4,#1
        calls tx
        jmpa  cc_UC,main
write_read_bad:
        movb  RL4,#2
        calls tx
        jmpa  cc_UC,main
write_deny:
        movb  RL4,#3
        calls tx
        jmpa  cc_UC,main
write_crc_bad:
        movb  RL4,#4
        calls tx
        jmpa  cc_UC,main
write_stale:
        movb  RL4,#5
        calls tx
        jmpa  cc_UC,main
write_failed:
        movb  RL4,#6
        calls tx
        jmpa  cc_UC,main
; A staged Soft-BSL entry (saved E740=1) must use the common boot finalizer
; before reset.  Direct/normal entry (0 or 3) remains state-preserving.
c_quit:
        calls rx
        cmpb  RL4,#0C3h
        jmpr  cc_NE,quit_bad
        calls rx
        cmpb  RL4,#03Ch
        jmpr  cc_NE,quit_bad
        jmpr  cc_UC,quit_finalize
c_recover:
        calls rx
        cmpb  RL4,#09Ch
        jmpr  cc_NE,quit_bad
        calls rx
        cmpb  RL4,#09Ch
        jmpr  cc_NE,quit_bad
quit_finalize:
        movb  RL4,ENTRYMODE
        cmpb  RL4,#1
        jmpr  cc_NE,quit_reset
        movb  RL4,#0
        movb  0E740h,RL4
        calls 01A62h
quit_reset:
        movb  RL4,#06h
        calls tx
        mov   0FFAEh,#0FF00h
        srvwdt
        srst
quit_spin:
        jmpr  cc_UC,quit_spin
quit_bad:
        movb  RL4,#0FFh
        calls tx
        jmpa  cc_UC,main

; Self-contained 24C04 I/O.  P3.3 is SCL, P3.12 is SDA.  The low-level
; transmitter returns r4=0 for ACK and r4=1 for NACK.
;
; Read bytes from r13 into [r12], length r14.  A failed transaction returns
; early, leaving the caller's prefill in every unread byte.
ee_read:
        mov   [-r0],r7
        mov   [-r0],r8
        mov   [-r0],r9
        mov   r7,r12
        mov   r8,r14
        mov   r9,r13
        cmp   r8,#0
        jmpr  cc_EQ,ee_read_done
ee_read_loop:
        mov   r13,r9
        mov   r14,#0A0h
        calls i2c_select
        cmp   r4,#0
        jmpr  cc_NE,ee_read_abort
        mov   r12,r9
        calls i2c_send
        cmp   r4,#0
        jmpr  cc_NE,ee_read_abort
        mov   r14,#0A1h
        calls i2c_select
        cmp   r4,#0
        jmpr  cc_NE,ee_read_abort
        calls i2c_recv
        movb  [r7],RL4
        calls i2c_nack
        calls i2c_stop
        srvwdt
        add   r7,#1
        add   r9,#1
        sub   r8,#1
        jmpr  cc_NE,ee_read_loop
        mov   r4,#0
        jmpr  cc_UC,ee_read_done
ee_read_abort:
        calls i2c_stop
        mov   r4,#1
ee_read_done:
        mov   r9,[r0+]
        mov   r8,[r0+]
        mov   r7,[r0+]
        rets

; Write bytes from [r12] to r13, length r14.  One byte per write cycle is
; intentionally page- and bank-safe across the complete 0x000..0x1FF range.
ee_write:
        mov   [-r0],r7
        mov   [-r0],r8
        mov   [-r0],r9
        mov   r7,r12
        mov   r8,r14
        mov   r9,r13
        cmp   r8,#0
        jmpr  cc_EQ,ee_write_done
ee_write_loop:
        mov   r13,r9
        mov   r14,#0A0h
        calls i2c_select
        cmp   r4,#0
        jmpr  cc_NE,ee_write_abort
        mov   r12,r9
        calls i2c_send
        cmp   r4,#0
        jmpr  cc_NE,ee_write_abort
        movb  RL4,[r7]
        movbz r12,RL4
        calls i2c_send
        cmp   r4,#0
        jmpr  cc_NE,ee_write_abort
        calls i2c_stop
        mov   r13,r9
        calls i2c_wait_ready
        cmp   r4,#0
        jmpr  cc_NE,ee_write_done
        srvwdt
        add   r7,#1
        add   r9,#1
        sub   r8,#1
        jmpr  cc_NE,ee_write_loop
        jmpr  cc_UC,ee_write_done
ee_write_abort:
        calls i2c_stop
ee_write_done:
        mov   r9,[r0+]
        mov   r8,[r0+]
        mov   r7,[r0+]
        rets

; START followed by the 24C04 control byte in r14 (A0 write / A1 read).
; Address bit A8 becomes control-byte bit 1.
i2c_select:
        bset  0FFC4h.12
        bset  0FFC6h.12
        calls i2c_delay
        bset  0FFC4h.3
        calls i2c_delay
        bclr  0FFC4h.12
        calls i2c_delay
        bclr  0FFC4h.3
        calls i2c_delay
        mov   r4,r13
        and   r4,#0100h
        shr   r4,#7
        or    r4,r14
        mov   r12,r4
        calls i2c_send
        rets

; Send low byte of r12, then return its slave ACK state in r4.
i2c_send:
        mov   r10,r12
        mov   r11,#8
i2c_send_loop:
        mov   r4,r10
        and   r4,#080h
        jmpr  cc_NE,i2c_send_one
        bclr  0FFC4h.12
        jmpr  cc_UC,i2c_send_drive
i2c_send_one:
        bset  0FFC4h.12
i2c_send_drive:
        bset  0FFC6h.12
        calls i2c_delay
        bset  0FFC4h.3
        calls i2c_delay
        bclr  0FFC4h.3
        calls i2c_delay
        shl   r10,#1
        sub   r11,#1
        jmpr  cc_NE,i2c_send_loop
        bclr  0FFC6h.12
        calls i2c_delay
        bset  0FFC4h.3
        calls i2c_delay
        mov   r4,#0
        jb    0FFC4h.12,i2c_send_nack
        jmpr  cc_UC,i2c_send_ack_done
i2c_send_nack:
        mov   r4,#1
i2c_send_ack_done:
        bclr  0FFC4h.3
        calls i2c_delay
        rets

; Receive one byte into RL4.  The caller emits ACK/NACK.
i2c_recv:
        mov   r10,#0
        mov   r11,#8
i2c_recv_loop:
        shl   r10,#1
        bclr  0FFC6h.12
        calls i2c_delay
        bset  0FFC4h.3
        calls i2c_delay
        jnb   0FFC4h.12,i2c_recv_zero
        or    r10,#1
i2c_recv_zero:
        bclr  0FFC4h.3
        calls i2c_delay
        sub   r11,#1
        jmpr  cc_NE,i2c_recv_loop
        mov   r4,r10
        rets

i2c_nack:
        bset  0FFC4h.12
        bset  0FFC6h.12
        calls i2c_delay
        bset  0FFC4h.3
        calls i2c_delay
        bclr  0FFC4h.3
        calls i2c_delay
        bclr  0FFC6h.12
        rets

i2c_stop:
        bclr  0FFC4h.12
        bset  0FFC6h.12
        calls i2c_delay
        bset  0FFC4h.3
        calls i2c_delay
        bset  0FFC4h.12
        calls i2c_delay
        bclr  0FFC6h.12
        rets

; ACK-poll after each physical byte write; return r4=1 on timeout.
i2c_wait_ready:
        mov   r15,#0400h
i2c_ready_loop:
        mov   r14,#0A0h
        calls i2c_select
        calls i2c_stop
        cmp   r4,#0
        jmpr  cc_EQ,i2c_ready_done
        srvwdt
        sub   r15,#1
        jmpr  cc_NE,i2c_ready_loop
i2c_ready_done:
        rets

; Conservative common phase delay, derived from the stock EEPROM bit-banger.
i2c_delay:
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        rets

fill_bytes:
        cmp   r6,#0
        jmpr  cc_EQ,fill_done
fill_loop:
        srvwdt
        movb  [r5],RL4
        add   r5,#1
        sub   r6,#1
        jmpr  cc_NE,fill_loop
fill_done:
        rets

; CRC16 reflected poly 0xA001, init 0xFFFF.  This is byte-identical in
; behavior to engines.softbsl.checksum._crc.
build_nibtbl:
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

nibfold:
        xor   r12,r4
        mov   r13,r12
        and   r13,#0Fh
        shl   r13,#1
        add   r13,#NIBTBL
        mov   r13,[r13]
        shr   r12,#4
        xor   r12,r13
        mov   r13,r12
        and   r13,#0Fh
        shl   r13,#1
        add   r13,#NIBTBL
        mov   r13,[r13]
        shr   r12,#4
        xor   r12,r13
        rets

crc16_run:
crc_loop:
        srvwdt
        cmp   r11,#0
        jmpr  cc_EQ,crc_done
        movb  RL4,[r5]
        movbz r4,RL4
        calls nibfold
        add   r5,#1
        sub   r11,#1
        jmpr  cc_UC,crc_loop
crc_done:
        rets

tx_crc:
        mov   r4,r12
        shr   r4,#8
        calls tx
        mov   r4,r12
        calls tx
        rets

; Polled ASC0 helpers, inherited 8E2/9600 from the staged loader.
rx_block:
        bset  0FFB0h.4
        bclr  0FF6Eh.7
rxb_wait:
        srvwdt
        jnb   0FF6Eh.7,rxb_wait
        movb  RL4,0FEB2h
        bclr  0FF6Eh.7
        rets

rx:
        bset  0FFB0h.4
        bclr  0FF6Eh.7
        push  r5
        mov   r5,#0FFFFh
rx_wait:
        srvwdt
        jnb   0FF6Eh.7,rx_check
        movb  RL4,0FEB2h
        bclr  0FF6Eh.7
        pop   r5
        rets
rx_check:
        sub   r5,#1
        jmpr  cc_NE,rx_wait
        pop   r5
        movb  RL4,#0
        rets

tx:
        bclr  0FFB0h.4
        movb  0FEB0h,RL4
tx_wait:
        srvwdt
        jnb   0FF6Ch.7,tx_wait
        bclr  0FF6Ch.7
        rets
