; C167 bulk agent. Installed by native 78/79 after the existing baud handoff.
; No ROM calls, interrupts, or flash manufacturer allowlist. The flash command
; set and bottom-boot geometry are the existing qualified ECU profile contract.
; Build resolves the buffer mapping, bus setup and identity per family.
; Request cmd/offset24BE/count16BE/data(P only)/CRC16BE, reflected A001/FFFF.
; Reply echoes header, status, data(I/R success only), CRC16BE.
; Scratch F600..F63F; buffer near9000..93FF; code E000..E7FF; stack FB00..FC00.
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
        mov DPP2,#@BUFFER_DPP@
@BUS_SETUP@
        mov r4,#0
        mov 0xf610,r4
        mov r4,#0x5342
        mov 0xf620,r4
        mov r4,#@IDENTITY@
        mov 0xf622,r4
        bclr S0CON.8
        bclr S0CON.9
        bclr S0CON.10
        bclr S0RIC.7
        bclr S0TIC.7
        ; Host waits for this byte before sending I; REN suppresses its echo.
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
        cmp r7,#0x46
        jmpa cc_EQ,finalize
        cmp r7,#0x52
        jmpr cc_EQ,range_data
        cmp r7,#0x50
        jmpr cc_EQ,range_data
        cmp r7,#0x45
        jmpa cc_EQ,erase
        mov r12,#5
        jmpa cc_UC,response
range_data:
        cmp r6,#2
        jmpa cc_C,bad_range
        mov r4,r8
        or r4,r6
        and r4,#1
        jmpa cc_NE,bad_range
        cmp r9,#8
        jmpa cc_NC,bad_range
        mov r4,r8
        add r4,r6
        jmpr cc_NC,range_ok
        cmp r9,#7
        jmpr cc_NE,range_ok
        cmp r4,#0
        jmpa cc_NE,bad_range
range_ok:
        cmp r7,#0x52
        jmpa cc_EQ,response
        calla cc_UC,write_permission
        mov r14,#0x9000
        mov r13,r6
program_word:
        mov r15,[r14]
        calla cc_UC,program_one
        cmp r12,#0
        jmpa cc_NE,response
        add r14,#2
        add r8,#2
        addc r9,#0
        sub r13,#2
        jmpr cc_NE,program_word
        jmpa cc_UC,response
program_one:
        mov r4,#0xa0
        calla cc_UC,flash_command
        mov r3,r9
        add r3,#0x10
        bset 0xff14.10
        exts r3,#1
        mov [r8],r15
        bclr 0xff14.10
        mov r1,#100
        calla cc_UC,flash_wait
        ret
erase:
        cmp r6,#0
        jmpa cc_NE,bad_range
        cmp r9,#8
        jmpa cc_NC,bad_range
        cmp r8,#0
        jmpr cc_EQ,erase_valid
        cmp r9,#0
        jmpa cc_NE,bad_range
        cmp r8,#0x4000
        jmpr cc_EQ,erase_valid
        cmp r8,#0x6000
        jmpr cc_EQ,erase_valid
        cmp r8,#0x8000
        jmpa cc_NE,bad_range
erase_valid:
        calla cc_UC,write_permission
        mov r4,#0x80
        calla cc_UC,flash_command
        mov r4,#0x30
        ; Erase's second unlock targets the requested sector, not AAAA.
        bset 0xff14.10
        mov r5,#0xaa
        exts 0x10,#1
        mov 0xaaaa,r5
        mov r5,#0x55
        exts 0x10,#1
        mov 0x5554,r5
        mov r3,r9
        add r3,#0x10
        exts r3,#1
        mov [r8],r4
        bclr 0xff14.10
        mov r15,#0xffff
        mov r1,#20000
        calla cc_UC,flash_wait
        jmpa cc_UC,response
write_permission:
        cmp r9,#0
        jmpr cc_NE,permission_ok
        mov r4,0xf610
        cmp r4,#1
        jmpr cc_EQ,permission_ok
        mov SP,#0xfc00
        jmpa cc_UC,bad_range
permission_ok:
        ret
identify:
        cmp r6,#4
        jmpa cc_NE,bad_range
        cmp r9,#0
        jmpa cc_NE,bad_range
        cmp r8,#1
        jmpa cc_UGT,bad_range
        mov 0xf610,r8
        jmpa cc_UC,response
reset:
        mov r4,r8
        or r4,r9
        or r4,r6
        jmpa cc_NE,bad_range
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
response_read:
        mov r3,r9
        add r3,#8
        exts r3,#1
        mov r15,[r8]
        mov r4,r15
        and r4,#0xff
        calla cc_UC,tx_crc
        mov r4,r15
        shr r4,#8
        calla cc_UC,tx_crc
        add r8,#2
        addc r9,#0
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
flash_command:
        bset 0xff14.10
        mov r5,#0xaa
        exts 0x10,#1
        mov 0xaaaa,r5
        mov r5,#0x55
        exts 0x10,#1
        mov 0x5554,r5
        exts 0x10,#1
        mov 0xaaaa,r4
        bclr 0xff14.10
        ret
flash_reset:
        bset 0xff14.10
        mov r4,#0xf0
        exts 0x10,#1
        mov 0xaaaa,r4
        bclr 0xff14.10
        ret
; Poll DQ6 with DQ5's required second check. Native T1 gives the inherited
; approximate 1ms timebase; program100 / erase20000 ticks. Final data comparison
; is required: a stable wrong word is not a successful write.
flash_wait:
        mov r2,r9
        add r2,#8
        mov 0xfe52,#0
flash_poll:
        srvwdt
        exts r2,#2
        mov r4,[r8]
        mov r5,[r8]
        xor r4,r5
        and r4,#0x40
        jmpr cc_EQ,flash_stable
        and r5,#0x20
        jmpr cc_NE,flash_recheck
        mov r4,0xfe52
        cmp r4,r1
        jmpr cc_C,flash_poll
        jmpr cc_UC,flash_error
flash_recheck:
        exts r2,#2
        mov r4,[r8]
        mov r5,[r8]
        xor r4,r5
        and r4,#0x40
        jmpr cc_NE,flash_error
flash_stable:
        exts r2,#1
        mov r4,[r8]
        cmp r4,r15
        jmpr cc_NE,flash_error
        calla cc_UC,flash_reset
        ret
flash_error:
        mov r12,#4
        calla cc_UC,flash_reset
        ret

; Completion reads actual flash. Only the final two marker pairs are programmed.
; F never resets; a failed check leaves the existing agent available for recovery.
finalize:
        mov r4,r8
        or r4,r9
        or r4,r6
        jmpa cc_NE,bad_range
        mov r12,#7
        mov r8,#0x3fe6
        mov r9,#0
        calla cc_UC,far_word
        mov r10,r4
        mov r8,#0x3c24
        calla cc_UC,far_word
        mov 0xf624,r4
        mov r8,#0x3c28
        mov r6,#1
        calla cc_UC,crc_first_range
        mov r12,#8
@PROGRAM_CRC_SETUP@
        calla cc_UC,crc_descriptor
        mov r12,#9
@CAL_CRC_EARLY@
@CAL_CRC_SETUP@
        calla cc_UC,crc_descriptor
cal_crc_done:
        mov r12,#10
        mov r8,#0x3fe8
        mov r9,#0
        mov r13,#8
        mov r14,#0x9000
        calla cc_UC,identity_record
        mov r12,#11
        mov r8,#@PROGRAM_ID_PTR@
        mov r9,#@DESCRIPTOR_SEG@
        calla cc_UC,indirect_pointer
        mov r13,#12
        mov r14,#0x9010
        calla cc_UC,identity_record
        mov r12,#12
        mov r8,#0x9000
        mov r13,#8
        mov r14,#0xfe00
        calla cc_UC,identity_compare
        mov r12,#13
        mov r8,#@CAL_ID_PTR@
        mov r9,#@DESCRIPTOR_SEG@
        calla cc_UC,indirect_pointer
        mov r13,#16
        mov r14,#0x9020
        calla cc_UC,identity_record
        mov r12,#14
        mov r8,#0x9010
        mov r13,#12
        mov r14,#0xfcc0
        calla cc_UC,identity_compare
        mov r12,#0
        mov r8,#@CAL_MARKER_OFFSET@
        mov r9,#@CAL_MARKER_SEG@
        mov r14,#0x2442
        mov r13,#0x9048
        calla cc_UC,commit_marker
        mov r8,#@PROGRAM_MARKER_OFFSET@
        mov r9,#@PROGRAM_MARKER_SEG@
        mov r14,#0x4890
        mov r13,#0x4224
        calla cc_UC,commit_marker
        jmpa cc_UC,response
finalize_error:
        mov SP,#0xfc00
        jmpa cc_UC,response
descriptor_error:
        mov r12,#16
        jmpr cc_UC,finalize_error

; Preserve raw far pointers so consumed-byte checks distinguish flash from RAM.
indirect_pointer:
        calla cc_UC,far_pointer
        mov r8,r4
        mov r9,r5
        ret
far_pointer:
        calla cc_UC,raw_pointer
        cmp r5,#15
        jmpa cc_UGT,descriptor_error
        ret
raw_pointer:
        calla cc_UC,far_word
        push r4
        calla cc_UC,far_word
        mov r5,r4
        pop r4
        ret
@CRC_TABLE_HELPER@
far_word:
        calla cc_UC,far_byte
        mov r5,r4
        calla cc_UC,far_byte
        shl r4,#8
        or r4,r5
        ret
far_byte:
        cmp r9,#16
        jmpa cc_NC,descriptor_error
@FLASH_READ_GUARD@
        mov r3,r9
        and r3,#7
        add r3,#8
        exts r3,#1
        movb RL4,[r8]
        movbz r4,RL4
        add r8,#1
        addc r9,#0
        ret
crc_descriptor:
        mov r4,r8
        and r4,#1
        jmpa cc_NE,descriptor_error
        calla cc_UC,far_word
        mov 0xf624,r4
        calla cc_UC,far_word
@CRC_COUNT@
@CRC_TABLE_CURSOR@
        mov r6,r4
        cmp r6,#0
        jmpr cc_NE,crc_first_range
        ; Stock visits the first range before comparing the range count.
        mov r6,#1
crc_first_range:
        mov r13,#1
crc_ranges:
        calla cc_UC,@CRC_POINTER@
        mov r0,r4
        mov r1,r5
        calla cc_UC,@CRC_POINTER@
        mov r14,r4
        mov r15,r5
        mov 0xf628,r8
        mov 0xf62a,r9
        cmp r13,#0
        jmpr cc_EQ,crc_first_checked
        mov r4,r0
        and r4,r1
        cmp r4,#0xffff
        jmpa cc_EQ,finalize_error
@CAL_FIRST_END_CHECK@
crc_first_checked:
        mov r13,#0
        ; Empty ranges consume no addresses, including unused nonflash STARTs.
        cmp r1,r15
        jmpa cc_UGT,crc_range_done
        jmpr cc_C,crc_nonempty
        cmp r0,r14
        jmpa cc_UGT,crc_range_done
crc_nonempty:
        cmp r1,#15
        jmpa cc_UGT,descriptor_error
        cmp r15,#15
        jmpa cc_UGT,descriptor_error
        mov r8,r0
        mov r9,r1
crc_flash:
        srvwdt
        calla cc_UC,far_byte
        calla cc_UC,crc_byte
        cmp r9,r15
        jmpr cc_C,crc_flash
        jmpr cc_NE,crc_range_done
        cmp r8,r14
        jmpr cc_ULE,crc_flash
crc_range_done:
        mov r8,0xf628
        mov r9,0xf62a
        sub r6,#1
        jmpr cc_NE,crc_ranges
@CAL_EXCEPTION_AFTER@
        cmp r10,0xf624
        jmpa cc_NE,finalize_error
        ret

; Pair order matches the stock copy selector: 0/1, 0/2, 1/2.
identity_record:
        mov 0xf630,r8
        mov 0xf632,r9
        mov 0xf634,r14
        mov r2,#0
identity_pair:
        mov r8,0xf630
        mov r9,0xf632
        mov r14,0xf634
        mov r0,r8
        mov r1,r9
        add r0,r13
        addc r1,#0
        cmp r2,#0
        jmpr cc_EQ,identity_pair_ready
        add r0,r13
        addc r1,#0
        cmp r2,#1
        jmpr cc_EQ,identity_pair_ready
        add r8,r13
        addc r9,#0
identity_pair_ready:
        mov r6,r13
identity_copy:
        calla cc_UC,far_byte
        movb [r14],RL4
        add r14,#1
        sub r6,#1
@IDENTITY_LAST_BYTE@
        mov r15,r4
        push r8
        push r9
        mov r8,r0
        mov r9,r1
        calla cc_UC,far_byte
        mov r0,r8
        mov r1,r9
        pop r9
        pop r8
        cmp r4,r15
        jmpr cc_NE,identity_next_pair
        cmp r6,#0
        jmpr cc_NE,identity_copy
identity_done:
        ret
identity_next_pair:
        add r2,#1
        cmp r2,#3
        jmpr cc_C,identity_pair
        jmpa cc_UC,finalize_error
identity_compare:
        shl r14,#1
        jmpr cc_NC,identity_compare_next
        movb RL4,[r8]
        movb RL5,[r8+#0x10]
        cmpb RL4,RL5
        jmpa cc_NE,finalize_error
identity_compare_next:
        add r8,#1
        sub r13,#1
        jmpr cc_NE,identity_compare
        ret
commit_marker:
        mov r3,r9
        add r3,#8
        exts r3,#2
        mov r4,[r8]
        mov r5,[r8+#2]
        cmp r4,r14
        jmpr cc_NE,marker_blank
        cmp r5,r13
        jmpr cc_EQ,marker_done
marker_blank:
        and r4,r5
        cmp r4,#0xffff
        jmpr cc_NE,marker_error
        mov r15,r14
        calla cc_UC,program_one
        cmp r12,#0
        jmpa cc_NE,finalize_error
        add r8,#2
        mov r15,r13
        calla cc_UC,program_one
        cmp r12,#0
        jmpa cc_NE,finalize_error
marker_done:
        ret
marker_error:
        mov r12,#15
        jmpa cc_UC,finalize_error
