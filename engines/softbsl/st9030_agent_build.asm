; st9030_agent_build.asm - temporary bounded MS41 C166/ST9030 proxy.
; Loads at 0xD800 through the existing CRC-checked Soft-BSL 0x5A loader.
;
; This agent deliberately exposes no arbitrary ASC1 transmit primitive.  Its
; active operations are seven fixed firmware-proven U3->U2 reply slots, one
; fixed stock token gate, and one fixed post-gate telemetry observation:
;   slot 0: 0x102 / 2 words       slot 4: 0x109 / 5 words
;   slot 1: 0x103 / 2 words       slot 5: 0x10A / 12 words
;   slot 2: 0x105 / 4 words       slot 6: 0x10E / 1 word
;   slot 3: 0x108 / 5 words
;
; Host protocol on ASC0 (all CRC values are CRC16/A001, init FFFF, high byte
; first on the wire):
;   'i'                    -> version,caps,slots,entry,crc16
;   's'                    -> status + S1CON/S1BG/S1TIC/S1RIC/S1EIC + crc16
;   'r' slot crc16('r'+slot)
;                          -> status,slot,count,raw_words_be...,crc16
;   'g' 'S' 'T' '9' '0' crc16('gST90')
;                          -> fixed stock-derived 10A/10C/10E gate transcript
;   't' 'S' 'T' '0' 'B' crc16('tST0B')
;                          -> fixed stock 10B/bounded-ready-poll transcript
;   'q' C3 3C             -> protected SRST; normalize E740 only from mode 1
;   'R' 9C 9C             -> shared Soft-BSL failure-cleanup alias
;
; Read response status: 0=ok, 1=invalid slot, 2=request CRC mismatch,
; 3=ASC1 transmit timeout, 4=ASC1 receive timeout, 5=ASC1 error interrupt.
; Raw response words retain bits 8:0 exactly as read from S1RBUF; unused upper
; bits are transmitted as read.  The only U2->U3 payload operation is the
; fixed stock-derived gate: receive 10A/12, require all ninth bits clear and
; byte 11=A0, rotate bytes 0..10 left by three, send 10C/11, then receive
; 10E/1 once.  Telemetry is exactly 10B + 0002,0000,0000,0000 and bounded
; 10E/1 polls; only A1 permits another FE52-paced poll.  It never sends 10D.
; There is no
; caller-selected command, length, payload, or retry count.
base 0d800h

BUF0       EQU 0E000h
GATETX     EQU 0E020h
GATE10E    EQU 0E038h
TELTIMES   EQU 0E040h
TELTERM    EQU 0E05Eh
ENTRYMODE  EQU 0E400h
SLOT       EQU 0E402h
WORDCOUNT  EQU 0E404h
GATEMAG    EQU 0E406h
GATEMAG1   EQU 0E407h
GATEMAG2   EQU 0E408h
GATEMAG3   EQU 0E409h
TELMAG     EQU 0E40Ah
TELMAG1    EQU 0E40Bh
TELMAG2    EQU 0E40Ch
TELMAG3    EQU 0E40Dh
BUF0LAST   EQU 0E016h
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
        movb  RL4,0E740h
        movb  ENTRYMODE,RL4
        movb  RL4,#0A5h
        calls tx
        calls build_nibtbl

main:
        srvwdt
        calls rx_block
        cmpb  RL4,#'i'
        jmpr  cc_NE,main_s
        jmpa  cc_UC,c_ident
main_s: cmpb  RL4,#'s'
        jmpr  cc_NE,main_r
        jmpa  cc_UC,c_snapshot
main_r: cmpb  RL4,#'r'
        jmpr  cc_NE,main_g
        jmpa  cc_UC,c_read
main_g: cmpb  RL4,#'g'
        jmpr  cc_NE,main_t
        jmpa  cc_UC,c_gate
main_t: cmpb  RL4,#'t'
        jmpr  cc_NE,main_q
        jmpa  cc_UC,c_telemetry
main_q: cmpb  RL4,#'q'
        jmpr  cc_NE,main_R
        jmpa  cc_UC,c_quit
main_R: cmpb  RL4,#'R'
        jmpr  cc_NE,c_nak
        jmpa  cc_UC,c_recover
c_nak: movb  RL4,#0FFh
        calls tx
        jmpa  cc_UC,main

; Version 5, caps bit0=fixed-slot raw-word read / bit1=ASC1 snapshot /
; bit2=fixed stock-derived 10A/10C/10E gate / bit3=fixed ready polling,
; seven slots, saved entry E740.  CRC covers those four bytes.
c_ident:
        mov   r12,#0FFFFh
        movb  RL4,#5
        calls tx_fold
        movb  RL4,#15
        calls tx_fold
        movb  RL4,#7
        calls tx_fold
        movb  RL4,ENTRYMODE
        calls tx_fold
        calls tx_crc
        jmpa  cc_UC,main

; Non-mutating ASC1 register snapshot. Status byte 0 plus five big-endian
; register words; CRC covers the complete 11-byte record before the CRC.
c_snapshot:
        mov   r12,#0FFFFh
        movb  RL4,#0
        calls tx_fold
        mov   r4,0FFB8h
        calls tx_word_fold
        mov   r4,0FEBCh
        calls tx_word_fold
        mov   r4,0FF72h
        calls tx_word_fold
        mov   r4,0FF74h
        calls tx_word_fold
        mov   r4,0FF76h
        calls tx_word_fold
        calls tx_crc
        jmpa  cc_UC,main

; CRC-protected fixed-slot request.  No caller-supplied length or command word
; reaches ASC1.
c_read:
        calls rx
        movb  SLOT,RL4
        calls rx
        movbz r6,RL4
        shl   r6,#8
        calls rx
        movbz r4,RL4
        or    r6,r4

        mov   r12,#0FFFFh
        movb  RL4,#'r'
        movbz r4,RL4
        calls nibfold
        movb  RL4,SLOT
        movbz r4,RL4
        calls nibfold
        cmp   r12,r6
        jmpr  cc_NE,read_crc_bad

        movb  RL4,SLOT
        cmpb  RL4,#0
        jmpr  cc_NE,slot_1
        mov   r4,#0102h
        mov   r11,#2
        jmpr  cc_UC,slot_go
slot_1: cmpb  RL4,#1
        jmpr  cc_NE,slot_2
        mov   r4,#0103h
        mov   r11,#2
        jmpr  cc_UC,slot_go
slot_2: cmpb  RL4,#2
        jmpr  cc_NE,slot_3
        mov   r4,#0105h
        mov   r11,#4
        jmpr  cc_UC,slot_go
slot_3: cmpb  RL4,#3
        jmpr  cc_NE,slot_4
        mov   r4,#0108h
        mov   r11,#5
        jmpr  cc_UC,slot_go
slot_4: cmpb  RL4,#4
        jmpr  cc_NE,slot_5
        mov   r4,#0109h
        mov   r11,#5
        jmpr  cc_UC,slot_go
slot_5: cmpb  RL4,#5
        jmpr  cc_NE,slot_6
        mov   r4,#010Ah
        mov   r11,#12
        jmpr  cc_UC,slot_go
slot_6: cmpb  RL4,#6
        jmpr  cc_NE,read_slot_bad
        mov   r4,#010Eh
        mov   r11,#1

slot_go:
        mov   WORDCOUNT,r11
        mov   r5,#BUF0
        calls s1_read_fixed
        cmp   r4,#0
        jmpr  cc_NE,read_status

        mov   r12,#0FFFFh
        movb  RL4,#0
        calls tx_fold
        movb  RL4,SLOT
        calls tx_fold
        mov   r4,WORDCOUNT
        calls tx_fold
        mov   r5,#BUF0
        mov   r11,WORDCOUNT
read_tx_loop:
        mov   r4,[r5]
        calls tx_word_fold
        add   r5,#2
        sub   r11,#1
        jmpr  cc_NE,read_tx_loop
        calls tx_crc
        jmpa  cc_UC,main

read_slot_bad:
        mov   r4,#1
        jmpr  cc_UC,read_status
read_crc_bad:
        mov   r4,#2
read_status:
        mov   r7,r4
        mov   r12,#0FFFFh
        mov   r4,r7
        calls tx_fold
        movb  RL4,SLOT
        calls tx_fold
        movb  RL4,#0
        calls tx_fold
        calls tx_crc
        jmpa  cc_UC,main

; One fixed, magic- and CRC-protected stock telemetry-ready observation.
; Request is exactly "tST0B" plus CRC16. Reply is always 66 bytes: status,
; issued-attempt count, terminal overall FE52 delta, fifteen raw 10E words,
; fifteen post-pacing overall FE52 deltas, CRC16. Capture the full request
; before clearing the transcript so a contiguous ASC0 tail cannot be lost
; while RX is unarmed.
c_telemetry:
        mov   r5,#TELMAG
        mov   r11,#4
tele_req_read:
        calls rx
        movb  [r5],RL4
        add   r5,#1
        sub   r11,#1
        jmpr  cc_NE,tele_req_read
        calls rx
        movbz r6,RL4
        shl   r6,#8
        calls rx
        movbz r4,RL4
        or    r6,r4
        calls gate_clear
        mov   r4,#0
        mov   WORDCOUNT,r4

        mov   r12,#0FFFFh
        movb  RL4,#'t'
        movbz r4,RL4
        calls nibfold
        mov   r5,#TELMAG
        mov   r11,#4
tele_crc_loop:
        movb  RL4,[r5]
        movbz r4,RL4
        calls nibfold
        add   r5,#1
        sub   r11,#1
        jmpr  cc_NE,tele_crc_loop
        cmp   r12,r6
        jmpr  cc_EQ,tele_crc_ok
        mov   r4,#1
        jmpa  cc_UC,tele_reply
tele_crc_ok:
        movb  RL4,TELMAG
        cmpb  RL4,#'S'
        jmpr  cc_NE,tele_magic_bad
        movb  RL4,TELMAG1
        cmpb  RL4,#'T'
        jmpr  cc_NE,tele_magic_bad
        movb  RL4,TELMAG2
        cmpb  RL4,#'0'
        jmpr  cc_NE,tele_magic_bad
        movb  RL4,TELMAG3
        cmpb  RL4,#'B'
        jmpr  cc_EQ,tele_10b
tele_magic_bad:
        mov   r4,#2
        jmpa  cc_UC,tele_reply

tele_10b:
        calls s1_init
        mov   r14,0FE52h
        calls s1_send_telemetry_noinit
        cmp   r4,#0
        jmpr  cc_EQ,tele_poll_window
        jmpa  cc_UC,tele_active_reply

tele_poll_window:
        mov   r9,0FE52h
        sub   r9,r14
        mov   TELTERM,r9
        cmp   r9,#0177h
        jmpr  cc_C,tele_poll
        mov   r4,#13
        jmpa  cc_UC,tele_reply

tele_poll:
        mov   r4,WORDCOUNT
        add   r4,#1
        mov   WORDCOUNT,r4
        sub   r4,#1
        shl   r4,#1
        mov   r3,r4
        mov   r8,0FE52h
        mov   r5,#BUF0
        add   r5,r3
        mov   r4,#010Eh
        mov   r11,#1
        calls s1_read_fixed_noinit
        cmp   r4,#0
        jmpr  cc_EQ,tele_pace
        add   r4,#3
        jmpa  cc_UC,tele_active_reply

tele_pace:
        mov   r10,#0FFFFh
tele_pace_wait:
        srvwdt
        mov   r6,0FE52h
        mov   r9,r6
        sub   r9,r8
        cmp   r9,#019h
        jmpr  cc_NC,tele_paced
        sub   r10,#1
        jmpr  cc_NE,tele_pace_wait
        mov   r9,r6
        sub   r9,r14
        mov   TELTERM,r9
        mov   r5,#TELTIMES
        add   r5,r3
        mov   [r5],r9
        mov   r4,#12
        jmpa  cc_UC,tele_reply

tele_paced:
        mov   r9,r6
        sub   r9,r14
        mov   TELTERM,r9
        mov   r5,#TELTIMES
        add   r5,r3
        mov   [r5],r9
        cmp   r9,#0177h
        jmpr  cc_C,tele_check
        mov   r4,#13
        jmpa  cc_UC,tele_reply

tele_check:
        mov   r5,#BUF0
        add   r5,r3
        mov   r4,[r5]
        mov   r6,r4
        and   r6,#0FF00h
        jmpr  cc_EQ,tele_status
        mov   r4,#9
        jmpa  cc_UC,tele_reply
tele_status:
        cmpb  RL4,#0A0h
        jmpr  cc_NE,tele_a1
        mov   r4,#0
        jmpa  cc_UC,tele_reply
tele_a1:
        cmpb  RL4,#0A1h
        jmpr  cc_NE,tele_ff
        mov   r4,WORDCOUNT
        cmp   r4,#15
        jmpr  cc_NE,tele_poll_window
        mov   r4,#14
        jmpa  cc_UC,tele_reply
tele_ff:
        cmpb  RL4,#0FFh
        jmpr  cc_NE,tele_other
        mov   r4,#10
        jmpa  cc_UC,tele_reply
tele_other:
        mov   r4,#11
        jmpa  cc_UC,tele_reply

tele_active_reply:
        mov   r7,r4
        mov   r9,0FE52h
        sub   r9,r14
        mov   TELTERM,r9
        mov   r4,r7

tele_reply:
        mov   r7,r4
        mov   r12,#0FFFFh
        mov   r4,r7
        calls tx_fold
        mov   r4,WORDCOUNT
        calls tx_fold
        mov   r4,TELTERM
        calls tx_word_fold
        mov   r5,#BUF0
        mov   r11,#15
tele_reply_words:
        mov   r4,[r5]
        calls tx_word_fold
        add   r5,#2
        sub   r11,#1
        jmpr  cc_NE,tele_reply_words
        mov   r5,#TELTIMES
        mov   r11,#15
tele_reply_times:
        mov   r4,[r5]
        calls tx_word_fold
        add   r5,#2
        sub   r11,#1
        jmpr  cc_NE,tele_reply_times
        calls tx_crc
        jmpa  cc_UC,main

; One fixed, magic- and CRC-protected stock-derived authorization-gate
; transaction. Request is exactly "gST90" plus CRC16. Reply is always 40
; bytes: status, 12 raw 10A words, the 11 derived 10C response bytes, one raw
; 10E word, CRC16. Status records whether the 10C transmit completed. Buffers
; are zeroed first so a failure returns deterministic evidence rather than
; stale RAM.
c_gate:
        mov   r5,#GATEMAG
        mov   r11,#4
gate_req_read:
        calls rx
        movb  [r5],RL4
        add   r5,#1
        sub   r11,#1
        jmpr  cc_NE,gate_req_read
        calls rx
        movbz r6,RL4
        shl   r6,#8
        calls rx
        movbz r4,RL4
        or    r6,r4
        calls gate_clear

        mov   r12,#0FFFFh
        movb  RL4,#'g'
        movbz r4,RL4
        calls nibfold
        mov   r5,#GATEMAG
        mov   r11,#4
gate_crc_loop:
        movb  RL4,[r5]
        movbz r4,RL4
        calls nibfold
        add   r5,#1
        sub   r11,#1
        jmpr  cc_NE,gate_crc_loop
        cmp   r12,r6
        jmpr  cc_EQ,gate_crc_ok
        mov   r4,#1
        jmpa  cc_UC,gate_reply
gate_crc_ok:
        movb  RL4,GATEMAG
        cmpb  RL4,#'S'
        jmpr  cc_EQ,gate_magic_1
        jmpa  cc_UC,gate_magic_bad
gate_magic_1:
        movb  RL4,GATEMAG1
        cmpb  RL4,#'T'
        jmpr  cc_EQ,gate_magic_2
        jmpa  cc_UC,gate_magic_bad
gate_magic_2:
        movb  RL4,GATEMAG2
        cmpb  RL4,#'9'
        jmpr  cc_EQ,gate_magic_3
        jmpa  cc_UC,gate_magic_bad
gate_magic_3:
        movb  RL4,GATEMAG3
        cmpb  RL4,#'0'
        jmpr  cc_EQ,gate_10a
        jmpa  cc_UC,gate_magic_bad

gate_10a:
        calls s1_init
        mov   r4,#010Ah
        mov   r11,#12
        mov   r5,#BUF0
        calls s1_read_fixed_noinit
        cmp   r4,#0
        jmpr  cc_EQ,gate_10a_validate
        jmpa  cc_UC,gate_reply
gate_10a_validate:
        mov   r5,#BUF0
        mov   r11,#12
gate_10a_bits:
        mov   r4,[r5]
        and   r4,#0100h
        jmpr  cc_EQ,gate_10a_next
        mov   r4,#6
        jmpa  cc_UC,gate_reply
gate_10a_next:
        add   r5,#2
        sub   r11,#1
        jmpr  cc_NE,gate_10a_bits
        movb  RL4,BUF0LAST
        cmpb  RL4,#0A0h
        jmpr  cc_EQ,gate_rotate
        mov   r4,#7
        jmpa  cc_UC,gate_reply

gate_rotate:
        mov   r5,#0E006h
        mov   r6,#GATETX
        mov   r11,#8
gate_rot_hi:
        movb  RL4,[r5]
        movb  [r6],RL4
        add   r5,#2
        add   r6,#1
        sub   r11,#1
        jmpr  cc_NE,gate_rot_hi
        mov   r5,#BUF0
        mov   r11,#3
gate_rot_lo:
        movb  RL4,[r5]
        movb  [r6],RL4
        add   r5,#2
        add   r6,#1
        sub   r11,#1
        jmpr  cc_NE,gate_rot_lo

        calls s1_send_gate_noinit
        cmp   r4,#0
        jmpr  cc_EQ,gate_10e
        jmpa  cc_UC,gate_reply

gate_10e:
        mov   r4,#010Eh
        mov   r11,#1
        mov   r5,#GATE10E
        calls s1_read_fixed_noinit
        cmp   r4,#0
        jmpr  cc_EQ,gate_10e_check
        cmp   r4,#3
        jmpr  cc_NE,gate_10e_rx_map
        mov   r4,#11
        jmpa  cc_UC,gate_reply
gate_10e_rx_map:
        cmp   r4,#4
        jmpr  cc_NE,gate_10e_error_map
        mov   r4,#12
        jmpa  cc_UC,gate_reply
gate_10e_error_map:
        mov   r4,#13
        jmpa  cc_UC,gate_reply
gate_10e_check:
        mov   r4,GATE10E
        mov   r6,r4
        and   r6,#0100h
        jmpr  cc_EQ,gate_10e_status
        mov   r4,#14
        jmpa  cc_UC,gate_reply
gate_10e_status:
        cmpb  RL4,#0A0h
        jmpr  cc_NE,gate_10e_a1
        mov   r4,#0
        jmpa  cc_UC,gate_reply
gate_10e_a1:
        cmpb  RL4,#0A1h
        jmpr  cc_NE,gate_10e_ff
        mov   r4,#15
        jmpa  cc_UC,gate_reply
gate_10e_ff:
        cmpb  RL4,#0FFh
        jmpr  cc_NE,gate_10e_other
        mov   r4,#16
        jmpa  cc_UC,gate_reply
gate_10e_other:
        mov   r4,#17
        jmpa  cc_UC,gate_reply

gate_magic_bad:
        mov   r4,#2

gate_reply:
        mov   r7,r4
        mov   r12,#0FFFFh
        mov   r4,r7
        calls tx_fold
        mov   r5,#BUF0
        mov   r11,#12
gate_reply_10a:
        mov   r4,[r5]
        calls tx_word_fold
        add   r5,#2
        sub   r11,#1
        jmpr  cc_NE,gate_reply_10a
        mov   r5,#GATETX
        mov   r11,#11
gate_reply_10c:
        movb  RL4,[r5]
        calls tx_fold
        add   r5,#1
        sub   r11,#1
        jmpr  cc_NE,gate_reply_10c
        mov   r4,GATE10E
        calls tx_word_fold
        calls tx_crc
        jmpa  cc_UC,main

gate_clear:
        mov   r5,#BUF0
        mov   r11,#96
        movb  RL4,#0
gate_clear_loop:
        movb  [r5],RL4
        add   r5,#1
        sub   r11,#1
        jmpr  cc_NE,gate_clear_loop
        rets

; Exact stock 1429861 ASC1 framing: S1BG=1, S1CON=801C (187500 baud,
; 9-bit asynchronous, two stop bits). Interrupts remain globally disabled;
; the agent polls the request bits in S1TIC/S1RIC/S1EIC.
s1_init:
        bset  0FFC4h.8
        bset  0FFC6h.8
        bset  0FFC4h.9
        bclr  0FFC6h.9
        mov   0FF76h,#0053h
        mov   0FEBCh,#0001h
        mov   0FFB8h,#801Ch
        mov   0FF72h,#0
        mov   0FF74h,#0
        bclr  0FF76h.7
        rets

; Input r4=fixed 9-bit command, r11=fixed receive count, r5=destination.
; Output r4=status, destination=raw 16-bit S1RBUF words.
s1_read_fixed:
        calls s1_init
s1_read_fixed_noinit:
        mov   r7,r4
        calls s1_tx_word
        cmp   r4,#0
        jmpr  cc_EQ,s1_rx_next
        rets

; Input r7=one fixed 9-bit or 8-bit word. Output r4=0, 3=timeout, 5=error.
s1_tx_word:
        bclr  0FF72h.7
        bclr  0FF74h.7
        bclr  0FF76h.7
        mov   0FEB8h,r7
        mov   r6,#0FFFFh
s1_tx_wait:
        srvwdt
        jb    0FF76h.7,s1_error
        jb    0FF72h.7,s1_tx_done
        sub   r6,#1
        jmpr  cc_NE,s1_tx_wait
        mov   r4,#3
        rets
s1_tx_done:
        bclr  0FF72h.7
        mov   r4,#0
        rets

; Fixed 10C header plus the eleven already-derived 8-bit bytes. Status mapping
; distinguishes header timeout (8), payload timeout (9), and ASC1 error (10).
s1_send_gate:
        calls s1_init
s1_send_gate_noinit:
        mov   r7,#010Ch
        calls s1_tx_word
        cmp   r4,#0
        jmpr  cc_EQ,s1_gate_payload
        cmp   r4,#3
        jmpr  cc_NE,s1_gate_error
        mov   r4,#8
        rets
s1_gate_payload:
        mov   r5,#GATETX
        mov   r11,#11
s1_gate_loop:
        movb  RL4,[r5]
        movbz r7,RL4
        calls s1_tx_word
        cmp   r4,#0
        jmpr  cc_NE,s1_gate_payload_fail
        add   r5,#1
        sub   r11,#1
        jmpr  cc_NE,s1_gate_loop
        rets
s1_gate_payload_fail:
        cmp   r4,#3
        jmpr  cc_NE,s1_gate_error
        mov   r4,#9
        rets
s1_gate_error:
        mov   r4,#10
        rets

; Fixed stock post-gate request: 10B header plus exactly 0002,0000,0000,0000.
; Output status is already phase-specific: 3=header timeout, 4=payload timeout,
; 5=ASC1 error.
s1_send_telemetry_noinit:
        mov   r7,#010Bh
        calls s1_tx_word
        cmp   r4,#0
        jmpr  cc_EQ,s1_tele_payload
        rets
s1_tele_payload:
        mov   r7,#2
        calls s1_tx_word
        cmp   r4,#0
        jmpr  cc_NE,s1_tele_payload_fail
        mov   r7,#0
        mov   r11,#3
s1_tele_zero_loop:
        calls s1_tx_word
        cmp   r4,#0
        jmpr  cc_NE,s1_tele_payload_fail
        sub   r11,#1
        jmpr  cc_NE,s1_tele_zero_loop
        rets
s1_tele_payload_fail:
        cmp   r4,#3
        jmpr  cc_NE,s1_tele_error
        mov   r4,#4
        rets
s1_tele_error:
        mov   r4,#5
        rets

s1_rx_next:
        mov   r6,#0FFFFh
s1_rx_wait:
        srvwdt
        jb    0FF76h.7,s1_error
        jb    0FF74h.7,s1_rx_have
        sub   r6,#1
        jmpr  cc_NE,s1_rx_wait
        mov   r4,#4
        rets
s1_rx_have:
        mov   r4,0FEBAh
        mov   [r5],r4
        add   r5,#2
        bclr  0FF74h.7
        sub   r11,#1
        jmpr  cc_NE,s1_rx_next
        mov   r4,#0
        rets
s1_error:
        bclr  0FF76h.7
        mov   r4,#5
        rets

; A staged Soft-BSL entry (saved E740=1) must use the common boot finalizer
; before reset. Direct/normal entry (0 or 3) remains state-preserving.
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

; CRC16 reflected polynomial A001, init FFFF. Byte-identical to
; engines.softbsl.checksum._crc.
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

tx_fold:
        movbz r4,RL4
        calls nibfold
        calls tx
        rets

tx_word_fold:
        mov   r6,r4
        shr   r4,#8
        calls tx_fold
        mov   r4,r6
        calls tx_fold
        rets

tx_crc:
        mov   r4,r12
        shr   r4,#8
        calls tx
        mov   r4,r12
        calls tx
        rets

; Polled ASC0 helpers, inherited 8E2/9600 or the staged loader's selected tier.
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
