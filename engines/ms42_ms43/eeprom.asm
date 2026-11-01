; Dedicated MS43 external2KiB EEPROM agent. No flash commands.
; Wire/CRC/upload contract matches agent.asm; identity is BE43.
base 0xE000
entry:
        bclr 0xff10.11
        mov SP,#0xfc00
        bclr S0CON.4
        bclr S0CON.8
        bclr S0CON.9
        bclr S0CON.10
        bclr S0RIC.7
        mov r14,#0xe096
; Build binds these fixed-width length/CRC operands; the host verifies them.
upload_length:
        mov r13,#0xffff
        mov r10,#0xffff
upload_byte:
        srvwdt
        mov r4,#0
        movb RL4,[r14]
        calla cc_UC,crc_byte
        add r14,#1
        sub r13,#1
        jmpr cc_NE,upload_byte
upload_crc:
        cmp r10,#0xffff
        jmpr cc_EQ,upload_verified
        mov r4,#0x5a
        calla cc_UC,upload_status
upload_failed:
        ; An invalid body must never execute, reset, or issue flash commands.
        srvwdt
        jmpr cc_UC,upload_failed
upload_verified:
        jmpa cc_UC,body_start
crc_byte:
        xor r10,r4
        mov r11,#8
crc_bit:
        shr r10,#1
        jmpr cc_NC,crc_next
        xor r10,#0xa001
crc_next:
        sub r11,#1
        jmpr cc_NE,crc_bit
        ret
upload_status:
        bclr S0TIC.7
        movb S0TBUF,RL4
upload_status_wait:
        srvwdt
        jnb S0TIC.7,upload_status_wait
        ret
        ; Keep the executable verifier inside the native 150-byte shadow.
@UPLOAD_PADDING@
body_start:
        mov DPP2,#0xc0
        ; Resident CS3 is already C000 (ADDRSEL3=00C0/BUSCON3=85BF).
        ; FF26 shadows that output latch; preserve every other output bit.
        calla cc_UC,eeprom_init
        mov r4,#0x4542
        mov 0xf620,r4
        mov r4,#0x3334
        mov 0xf622,r4
        bclr S0CON.8
        bclr S0CON.9
        bclr S0CON.10
        bclr S0RIC.7
        bclr S0TIC.7
        mov r4,#0xa5
        calla cc_UC,upload_status
main:
        mov SP,#0xfc00
        mov r4,#0
        mov 0xf600,r4
        mov 0xf602,r4
        mov 0xf604,r4
        mov r12,#0
        mov r10,#0xffff
        mov r14,#0xf600
        mov r13,#6
        bset S0CON.4
idle_poll:
        srvwdt
        jnb S0RIC.7,idle_poll
header_rx:
        calla cc_UC,rx
        movb [r14],RL4
        add r14,#1
        calla cc_UC,crc_byte
        sub r13,#1
        jmpr cc_NE,header_rx
        movbz r7,0xf600
        movbz r9,0xf601
        movbz r8,0xf602
        shl r8,#8
        movbz r4,0xf603
        or r8,r4
        movbz r6,0xf604
        shl r6,#8
        movbz r4,0xf605
        or r6,r4
        cmp r6,#0x400
        jmpa cc_UGT,bad_frame_range
        cmp r7,#0x50
        jmpr cc_NE,receive_crc
        mov r14,#0x9000
        mov r13,r6
        cmp r13,#0
        jmpr cc_EQ,receive_crc
payload_rx:
        calla cc_UC,rx
        movb [r14],RL4
        add r14,#1
        calla cc_UC,crc_byte
        sub r13,#1
        jmpr cc_NE,payload_rx
receive_crc:
        calla cc_UC,rx
        mov r15,r4
        shl r15,#8
        calla cc_UC,rx
        or r15,r4
        cmp r15,r10
        jmpa cc_NE,bad_crc
        cmp r7,#0x49
        jmpa cc_EQ,identify
        cmp r7,#0x58
        jmpa cc_EQ,reset
        cmp r7,#0x52
        jmpr cc_EQ,range_data
        cmp r7,#0x50
        jmpr cc_EQ,range_data
        mov r12,#5
        jmpa cc_UC,response
range_data:
        cmp r9,#0
        jmpa cc_NE,bad_range
        cmp r6,#2
        jmpa cc_C,bad_range
        mov r4,r8
        or r4,r6
        and r4,#1
        jmpa cc_NE,bad_range
        cmp r8,#0x800
        jmpa cc_NC,bad_range
        mov r4,r8
        add r4,r6
        cmp r4,#0x800
        jmpa cc_UGT,bad_range
        cmp r7,#0x50
        jmpr cc_NE,read_data
        cmp r6,#2
        jmpa cc_NE,bad_range
        calla cc_UC,eeprom_program
        jmpa cc_UC,response
read_data:
        mov r14,#0x9000
        mov r13,r6
read_word:
        calla cc_UC,eeprom_read
        cmp r12,#0
        jmpa cc_NE,response
        mov [r14],r15
        add r14,#2
        add r8,#2
        sub r13,#2
        jmpr cc_NE,read_word
        jmpa cc_UC,response
identify:
        cmp r6,#4
        jmpa cc_NE,bad_range
        mov r4,r8
        or r4,r9
        jmpa cc_NE,bad_range
        jmpa cc_UC,response
reset:
        mov r4,r8
        or r4,r9
        or r4,r6
        jmpa cc_NE,bad_range
        calla cc_UC,eeprom_disable
        cmp r12,#0
        jmpa cc_NE,response
        calla cc_UC,send_response
        ; S0TIR precedes the final stop bit (C167CR manual 11-7/11-16).
        ; Two inherited T1 ticks guarantee at least one full ~1ms tick elapsed.
        mov r5,0xfe52
reset_wait:
        srvwdt
        mov r4,0xfe52
        sub r4,r5
        cmp r4,#2
        jmpr cc_C,reset_wait
        srst
bad_crc:
        mov r12,#1
        jmpa cc_UC,response
bad_range:
        mov r12,#2
response:
        calla cc_UC,send_response
response_done:
        jmpa cc_UC,main
send_response:
        mov r10,#0xffff
        mov r14,#0xf600
        mov r13,#6
response_header:
        movb RL4,[r14]
        movbz r4,RL4
        calla cc_UC,tx_crc
        add r14,#1
        sub r13,#1
        jmpr cc_NE,response_header
        mov r4,r12
        calla cc_UC,tx_crc
        cmp r12,#0
        jmpr cc_NE,response_crc
        cmp r7,#0x49
        jmpr cc_EQ,response_identity
        cmp r7,#0x52
        jmpr cc_NE,response_crc
        mov r13,r6
        mov r14,#0x9000
response_read:
        mov r15,[r14]
        mov r4,r15
        and r4,#0xff
        calla cc_UC,tx_crc
        mov r4,r15
        shr r4,#8
        calla cc_UC,tx_crc
        add r14,#2
        sub r13,#2
        jmpr cc_NE,response_read
        jmpr cc_UC,response_crc
response_identity:
        mov r14,#0xf620
        mov r13,#4
identity_bytes:
        movb RL4,[r14]
        movbz r4,RL4
        calla cc_UC,tx_crc
        add r14,#1
        sub r13,#1
        jmpr cc_NE,identity_bytes
response_crc:
        mov r4,r10
        shr r4,#8
        calla cc_UC,tx
        mov r4,r10
        and r4,#0xff
        calla cc_UC,tx
        ret
tx_crc:
        calla cc_UC,crc_byte
        calla cc_UC,tx
        ret
; Receiver is disabled for the entire response, suppressing K-line local echo.
; RX never clears a pending byte before consuming it. Inter-byte faults abandon
; the whole request and require a quiet interval; no partial P can reach flash.
rx:
        bset S0CON.4
        mov r5,0xfe52
rx_poll:
        srvwdt
        mov r4,S0CON
        and r4,#0x0700
        jmpr cc_NE,rx_uart
        jb S0RIC.7,rx_ready
        mov r4,0xfe52
        sub r4,r5
        cmp r4,#500
        jmpr cc_C,rx_poll
        mov r12,#3
        jmpr cc_UC,rx_abort
rx_uart:
        mov r12,#6
        bclr S0CON.8
        bclr S0CON.9
        bclr S0CON.10
        jmpr cc_UC,rx_abort
bad_frame_range:
        mov r12,#2
rx_abort:
        mov SP,#0xfc00
rx_quiet:
        mov r5,0xfe52
rx_drain:
        srvwdt
        jnb S0RIC.7,rx_quiet_check
        mov r4,S0RBUF
        bclr S0RIC.7
        jmpr cc_UC,rx_quiet
rx_quiet_check:
        mov r4,0xfe52
        sub r4,r5
        cmp r4,#500
        jmpr cc_C,rx_drain
        jmpa cc_UC,response
rx_ready:
        movbz r4,S0RBUF
        bclr S0RIC.7
        ret
tx:
        bclr S0CON.4
        bclr S0TIC.7
        movb S0TBUF,RL4
        mov r5,0xfe52
tx_poll:
        srvwdt
        jb S0TIC.7,tx_done
        mov r4,0xfe52
        sub r4,r5
        cmp r4,#500
        jmpr cc_C,tx_poll
        ; No valid response can be promised after a broken transmitter.
        mov SP,#0xfc00
        jmpa cc_UC,main
tx_done:
        ret
; Exact 430069 EB280/EB420 protocol, using the resident output-latch map.
; SSCBR=17h, 16-bit MSB-first command phase C23F; READ data phase C21F.
; Command words: READ1800|address, WRITE1400|address, EWEN1300, EWDS1000.
; No ROM calls/interrupts/flash operations. All public offsets are byte offsets.
eeprom_init:
        mov 0xffb2,#0
        mov 0xff72,#0
        mov 0xff74,#0
        mov 0xff76,#0
        bclr 0xffc6.8
        ; C167 requires one instruction between a DP3 write and P3 access.
        nop
        bset 0xffc4.9
        bset 0xffc6.9
        nop
        bset 0xffc4.13
        bset 0xffc6.13
        mov r4,#0x17
        mov 0xf0b4,r4
        calla cc_UC,eeprom_deselect
        ret
eeprom_mode:
        bclr 0xffb2.15
        mov 0xffb2,#0x423f
        bset 0xffb2.15
        ret
eeprom_deselect:
        bclr 0xff26.7
        mov 0xc000,0xff26
        ret
eeprom_select:
        calla cc_UC,eeprom_mode
        bset 0xff26.7
        mov 0xc000,0xff26
        ; ST DS1077: tSHQV/tSLSH <=200ns. Eight NOPs plus call overhead
        ; exceed that at24MHz without adding a millisecond to every word.
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        mov r5,0xfe52
ready_poll:
        srvwdt
        jb 0xffc4.8,ready_done
        mov r4,0xfe52
        sub r4,r5
        cmp r4,#10
        jmpr cc_C,ready_poll
        mov r12,#17
ready_done:
        ret
ssc_word:
        bclr 0xff74.7
        mov 0xf0b0,r4
        mov r5,0xfe52
ssc_poll:
        srvwdt
        jb 0xff74.7,ssc_done
        mov r4,0xfe52
        sub r4,r5
        cmp r4,#2
        jmpr cc_C,ssc_poll
        mov r12,#17
        ret
ssc_done:
        mov r4,0xffb2
        and r4,#0x0f00
        jmpr cc_EQ,ssc_result
        mov r12,#19
ssc_result:
        mov r4,0xf0b2
        ret
eeprom_read:
        calla cc_UC,eeprom_select
        cmp r12,#0
        jmpr cc_NE,eeprom_read_done
        mov r4,r8
        shr r4,#1
        or r4,#0x1800
        calla cc_UC,ssc_word
        cmp r12,#0
        jmpr cc_NE,eeprom_read_done
        ; Native EB450 holds SCLK low before SSC hands the pin to GPIO.
eeprom_read_clock_hold:
        bmov 0xffc4.13,0xffb2.6
        bclr 0xffb2.15
        mov 0xffb2,#0x421f
        bset 0xffb2.15
        bset 0xffc4.13
        mov r4,#0
        calla cc_UC,ssc_word
        mov r15,r4
eeprom_read_done:
        calla cc_UC,eeprom_deselect
        ret
eeprom_program:
        ; Idempotent retransmission: skip a word already at the target value.
        calla cc_UC,eeprom_read
        cmp r12,#0
        jmpr cc_NE,eeprom_program_done
        mov r4,0x9000
        cmp r15,r4
        jmpr cc_EQ,eeprom_program_cleanup
        calla cc_UC,eeprom_select
        cmp r12,#0
        jmpr cc_NE,eeprom_program_cleanup
        mov r4,#0x1300
        calla cc_UC,ssc_word
        calla cc_UC,eeprom_deselect
        cmp r12,#0
        jmpr cc_NE,eeprom_program_cleanup
        calla cc_UC,eeprom_select
        cmp r12,#0
        jmpr cc_NE,eeprom_program_cleanup
        mov r4,r8
        shr r4,#1
        or r4,#0x1400
        calla cc_UC,ssc_word
        cmp r12,#0
        jmpr cc_NE,eeprom_program_cleanup
        mov r4,0x9000
        calla cc_UC,ssc_word
        ; S falling starts the automatic erase/write cycle (maximum5ms).
        calla cc_UC,eeprom_deselect
        cmp r12,#0
        jmpr cc_NE,eeprom_program_cleanup
        calla cc_UC,eeprom_read
        cmp r12,#0
        jmpr cc_NE,eeprom_program_cleanup
        mov r4,0x9000
        cmp r15,r4
        jmpr cc_EQ,eeprom_program_cleanup
        mov r12,#18
eeprom_program_cleanup:
        calla cc_UC,eeprom_disable
eeprom_program_done:
        ret
eeprom_disable:
        ; Always attempt EWDS after an enabled write. Preserve its original
        ; failure if cleanup also fails; successful replies require both.
        push r12
        mov r12,#0
        calla cc_UC,eeprom_deselect
        calla cc_UC,eeprom_select
        cmp r12,#0
        jmpr cc_NE,eeprom_disable_done
        mov r4,#0x1000
        calla cc_UC,ssc_word
eeprom_disable_done:
        calla cc_UC,eeprom_deselect
        pop r4
        cmp r4,#0
        jmpr cc_EQ,eeprom_disable_return
        mov r12,r4
eeprom_disable_return:
        ret
