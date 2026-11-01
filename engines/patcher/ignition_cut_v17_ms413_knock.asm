; V17 bounded software provenance for native knock observations.
; D805=A7 ownership marker; D806=clean window phases; D807=open phase bit;
; D808=pre-cut reply drained; D809=request qualified; D80A=snapshot qualified;
; D80B=retained channel validity. No learned native value is cleared.
; Recovery requires native handoffs, never elapsed time. Physical U3 result
; age and electrical timing remain outside this C166 software contract.
base 0x3F000
dirty:
        push PSW
        bclr 0xFF10.11
        push r4
        mov r4,#0
        mov 0xD806,r4
        mov 0xD808,r4
        mov 0xD80A,r4
        movb RL4,#0xA7
        movb 0xD805,RL4
        pop r4
        pop PSW
        rets

; Initialize conservatively after startup. IRQ dirty can never be lost to init.
initialize:
        movb RL4,0xD805
        cmpb RL4,#0xA7
        jmpr cc_EQ,initialized
        calls dirty
initialized:
        rets

; R4 scratch, zero means no currently published patch combustion modification.
active:
        calls initialize
        movb RL4,0xD801
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,active_clear
        movb RL4,0xD801
        andb RL4,#7
        jmpr cc_NE,active_dirty
        movb RL4,0xD803
        cmpb RL4,#0xC3
        jmpr cc_NE,active_clear
active_dirty:
        calls dirty
        mov r4,#1
        rets
active_clear:
        mov r4,#0
        rets

window_start:
        push PSW
        bclr 0xFF10.11
        push r4
        push r5
        calls active
        jmpr cc_NE,window_start_done
        movb RL4,0xD808
        cmpb RL4,#1
        jmpr cc_NE,window_start_done
        movbz r4,0xFA80
        cmp r4,#5
        jmpr cc_UGT,window_start_done
        mov r5,#1
        shl r5,r4
        movb 0xD807,RL5
window_start_done:
        pop r5
        pop r4
        pop PSW
        rets

window_end:
        push PSW
        bclr 0xFF10.11
        push r4
        calls active
        jmpr cc_NE,window_end_done
        movb RL4,0xD807
        orb 0xD806,RL4
        movb RL4,#0
        movb 0xD807,RL4
window_end_done:
        pop r4
        pop PSW
        rets

; Native serialized ASC1 dispatcher: the index sentinel prevents an old raw
; pair being mistaken for the result of this newly issued two-byte PEC receive.
request:
        push PSW
        bclr 0xFF10.11
        push r4
        calls active
        jmpr cc_NE,request_invalid
        movb RL4,0xD806
        cmpb RL4,#0x3F
        jmpr cc_NE,request_invalid
        movb RL4,#1
        jmpr cc_UC,request_save
request_invalid:
        movb RL4,#0
request_save:
        movb 0xD809,RL4
        movb RL4,0xD80B
        cmpb RL4,#0x3F
        jmpr cc_EQ,request_done
        movb RL4,#0x0C
        movb 0xF6DF,RL4
request_done:
        pop r4
        pop PSW
        mov r1,#0xF6DE
        rets

; All twelve native crank sites retain their following word store and index
; sentinel. A real accepted raw pair drains an old request before clean windows.
snapshot:
        push PSW
        bclr 0xFF10.11
        push r4
        calls active
        movb RL4,#0
        movb 0xD80A,RL4
        mov r6,0xF6DE
        cmpb RH6,#5
        jmpr cc_UGT,snapshot_done
        movb RL4,0xD809
        movb 0xD80A,RL4
        movb RL4,#1
        movb 0xD808,RL4
snapshot_done:
        pop r4
        pop PSW
        mov r6,r6
        rets

; Serialize the complete native Task04 index/value/tag read and publication.
; Preserve its exact GPR and condition-code liveouts, including invalid index.
ingress:
        push PSW
        bclr 0xFF10.11
        push r4
        push r5
        calls active
        movbz r1,0xF6E1
        movb 0xF493,RL1
        cmp r1,#5
        jmpr cc_UGT,ingress_native_invalid
        movb RL2,0xF6E0
        movb [r1+#0xFC76],RL2
        push PSW
        mov r5,#1
        shl r5,r1
        movb RL4,0xD80A
        cmpb RL4,#1
        jmpr cc_NE,ingress_invalid
        orb 0xD80B,RL5
        jmpr cc_UC,ingress_done
ingress_invalid:
        cpl r5
        andb 0xD80B,RL5
ingress_done:
        jmpr cc_UC,ingress_restore
ingress_native_invalid:
        push PSW
ingress_restore:
        pop r4
        and r4,#0xF7FF
        mov r5,SP
        mov r5,[r5+#4]
        and r5,#0x0800
        or r4,r5
        mov r5,SP
        mov [r5+#4],r4
        pop r5
        pop r4
        pop PSW
        jmps 0x34F12

; The foreground native sample bank is stable during this serialized task.
; Freeze before filtering, event detection, cell transfer, and correction write.
; The native setup before this boundary still updates gain/window calibrations.
prefilter:
        push r4
        push r5
        calls active
        jmpr cc_NE,blocked
        mov r5,#1
        shl r5,r6
        movb RL4,0xD80B
        andb RL4,RL5
        jmpr cc_EQ,blocked
        pop r5
        pop r4
        mov r4,r6
        shl r4,#1
        jmps 0x2A7BC
blocked:
        mov r4,0xFD1C
        and r4,#4
        jmpr cc_EQ,frozen
        jb 0xFD32.14,native_fault
        jb 0xFD32.15,native_fault
        jb 0xFD5E.8,native_fault
        jb 0xFD5E.10,native_fault
        mov r4,0xEA0C
        and r4,#0x1000
        jmpr cc_NE,native_fault
frozen:
        pop r5
        pop r4
        mov r4,#0xFBFF
        and 0xFD1C,r4
        movb RL7,[r6+#0xE9CC]
        movbz r7,RL7
        shl r7,#8
        jmps 0x2AD4C
native_fault:
        pop r5
        pop r4
        jmps 0x2AC00

; Keep global diagnostics and native fault processing. Only the whole global
; learning/counter transaction waits for all six retained channels to be fresh.
global_gate:
        push PSW
        push r4
        calls active
        jmpr cc_NE,global_blocked
        movb RL4,0xD80B
        cmpb RL4,#0x3F
        jmpr cc_NE,global_blocked
        mov r4,0xFD1C
        and r4,#0x0400
        jmpr cc_EQ,global_blocked
        pop r4
        pop PSW
        jmps 0x2A21C
global_blocked:
        pop r4
        pop PSW
global_skip:
        jmps 0x2A2AC

; Preserve the native compare and port behavior, including the resulting PSW.
window_0:
        cmpb RL0,0xFA90
        jmpr cc_NE,window_0_end
        bset 0xFFC4.4
        calls window_start
        jmps 0x434C
window_0_end:
        cmpb RL0,0xFA91
        jmpr cc_NE,window_0_done
        bclr 0xFFC4.4
        calls window_end
window_0_done:
        jmps 0x434C

; Preserve the native compare and port behavior, including the resulting PSW.
window_1:
        cmpb RL0,0xFA90
        jmpr cc_NE,window_1_end
        bset 0xFFC4.4
        calls window_start
        jmps 0x5172
window_1_end:
        cmpb RL0,0xFA91
        jmpr cc_NE,window_1_done
        bclr 0xFFC4.4
        calls window_end
window_1_done:
        jmps 0x5172
