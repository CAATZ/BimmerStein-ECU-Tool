; V20 leaves the native crank vectors, window pins, PEC receive and retirement intact.
; D805 = initialized A7 / modified timing E7. D806 = drained channel mask.
; D807 = empty 0 / expecting 1 / accepted 2 / invalidated by charge 3.
; D808 holds the snapshot at request, then the accepted pair.
; D80A = accepted current reply. D80B = six retained fresh channel bits.
base 0x3F000
dirty:
        push PSW
        bclr 0xFF10.11
        push r4
        mov r4,#0
        mov 0xD806,r4
        mov 0xD808,r4
        mov 0xD80A,r4
        movb RL4,0xD805
        cmpb RL4,#0xE7
        jmpr cc_NE,dirty_unarmed
        movb RL4,0xD803
        cmpb RL4,#0xC3
        jmpr cc_EQ,dirty_owned
dirty_unarmed:
        movb RL4,#0xA7
        movb 0xD805,RL4
dirty_owned:
        pop r4
        pop PSW
        rets

; Only an actual final-event angle change arms the timing episode. Holding
; eligibility alone never arms it. An armed episode remains conservative until
; the published C3 episode ends; queued modified events cannot be erased by a
; later no-op/tie event. Dirty/recovery state remains latched after that release.
timing_dirty:
        push PSW
        bclr 0xFF10.11
        calls dirty
        push r4
        movb RL4,#0xE7
        movb 0xD805,RL4
        pop r4
        pop PSW
        rets

; The event owner records release even if no foreground observation occurs
; between episodes. A nested newer C3 publication must not lose its latch.
; Clearing the episode marker never makes pending observations fresh.
timing_release:
        push PSW
        bclr 0xFF10.11
        push r4
        movb RL4,0xD803
        cmpb RL4,#0xC3
        jmpr cc_EQ,timing_release_done
        movb RL4,0xD805
        cmpb RL4,#0xE7
        jmpr cc_NE,timing_release_done
        movb RL4,#0xA7
        movb 0xD805,RL4
timing_release_done:
        pop r4
        pop PSW
        rets

; Before the first real intervention all native observations retain their ordinary
; eligibility. A prior dirty IRQ already published A7/E7 and wins over initialization.
initialize:
        movb RL4,0xD805
        andb RL4,#0xBF
        cmpb RL4,#0xA7
        jmpr cc_EQ,initialized
        ; A real CC6 skip can precede the first foreground initialization.
        ; Its invalidation token survives a subsequent permitted charge.
        movb RL4,0xD807
        cmpb RL4,#3
        jmpr cc_EQ,initialize_marker
        movb RL4,#0x3F
        movb 0xD806,RL4
        movb 0xD80B,RL4
initialize_marker:
        movb RL4,#0xA7
        movb 0xD805,RL4
initialized:
        rets

active:
        push PSW
        bclr 0xFF10.11
        calls initialize
        movb RL4,0xD801
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,active_release
        movb RL4,0xD802
        andb RL4,#0x20
        jmpr cc_NE,active_dirty
        movb RL4,0xD801
        andb RL4,#4
        jmpr cc_NE,active_dirty
        movb RL4,0xD803
        cmpb RL4,#0xC3
        jmpr cc_NE,active_release
        movb RL4,0xD805
        cmpb RL4,#0xE7
        jmpr cc_NE,active_clear
active_dirty:
        calls dirty
        mov r4,#1
        jmpr cc_UC,active_return
active_release:
        movb RL4,#0xA7
        movb 0xD805,RL4
active_clear:
        mov r4,#0
active_return:
        pop PSW
        cmp r4,#0
        rets

; Only the second PEC transfer of 0102 can replace the retired channel (12)
; with a valid index. Keep the original word for the diagnostics and remember
; the previous snapshot so an old Task04 visit cannot pass as the new reply.
request:
        push PSW
        push r4
        movb RL4,0xD80B
        cmpb RL4,#0x3F
        jmpr cc_EQ,request_done
        push PSW
        bclr 0xFF10.11
        calls active
        jmpr cc_NE,request_unproven
        movb RL4,0xF6DF
        cmpb RL4,#12
        jmpr cc_NE,request_unproven
        mov r4,0xF6E0
        mov 0xD808,r4
        movb RL4,#1
        jmpr cc_UC,request_store
request_unproven:
        movb RL4,#0
request_store:
        movb 0xD807,RL4
        movb RL4,#0
        movb 0xD80A,RL4
        pop PSW
request_done:
        pop r4
        pop PSW
        mov r1,#0xF6DE
        rets

; The ordinary path executes the stock publication unmasked. During recovery
; serialize a single captured word with its provenance. A new request or dirty
; event revokes the old token. Consecutive Task04 visits cannot credit it twice.
ingress:
        push PSW
        push r4
        movb RL4,0xD80B
        cmpb RL4,#0x3F
        jmpr cc_NE,ingress_recovery
        pop r4
        pop PSW
        movbz r1,0xF6E1
        jmps 0x34F02
ingress_recovery:
        bclr 0xFF10.11
        push r5
        mov r4,0xF6E0
        movbz r1,RH4
        movb 0xF493,RL1
        cmp r1,#5
        jmpr cc_UGT,ingress_exit
        movb RL2,RL4
        movb [r1+#0xFC76],RL2
        push PSW
        movbz r5,0xD807
        cmp r5,#2
        jmpr cc_NE,ingress_pending
        cmp r4,0xD808
        jmpr cc_EQ,ingress_restore
        jmpr cc_UC,ingress_invalid
ingress_pending:
        cmp r5,#1
        jmpr cc_NE,ingress_invalid
        cmp r4,0xD808
        jmpr cc_EQ,ingress_invalid
        movb RL4,0xF6DF
        cmpb RL4,#12
        jmpr cc_NE,ingress_invalid
        mov r4,r1
        add r4,#1
        cmp r4,#6
        jmpr cc_NE,check_phase
        mov r4,#0
check_phase:
        cmpb RL4,0xFC9F
        jmpr cc_NE,ingress_invalid
        mov r4,0xF6E0
        mov 0xD808,r4
        movb RL4,#2
        movb 0xD807,RL4
        movb RL4,#1
        movb 0xD80A,RL4
        movb RL4,0xD806
        cmpb RL4,#0x3F
        jmpr cc_EQ,ingress_fresh
        mov r5,#1
        shl r5,r1
        orb 0xD806,RL5
        jmpr cc_UC,ingress_restore
ingress_fresh:
        mov r5,#1
        shl r5,r1
        orb 0xD80B,RL5
        jmpr cc_UC,ingress_restore
ingress_invalid:
        mov r5,#1
        shl r5,r1
        cpl r5
        andb 0xD80B,RL5
ingress_restore:
        pop PSW
ingress_exit:
        push PSW
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

