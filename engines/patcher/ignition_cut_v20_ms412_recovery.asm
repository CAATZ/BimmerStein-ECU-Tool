; MS41.2 1406464 and MS41.3 SS1v2 share these native entry points.
base 0x3E3E8
        jmpa cc_UC,committed_cut
        jmpa cc_UC,misfire
        jmpa cc_UC,count
        jmpa cc_UC,segment

; This entry also serves the existing bank/electrical observation callers.
; D802.5 is an actual omitted charge, D801.2 is the Launch-owned fuel stage.
committed_cut:
        movb RL4,0xD801
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,clear
        movb RL4,0xD802
        andb RL4,#0x20
        jmpr cc_NE,cut_observed
        movb RL4,0xD801
        andb RL4,#4
        jmpr cc_EQ,done
cut_observed:
        movb RL4,#15
        orb 0xD802,RL4
done:
        rets
clear:
        mov r4,#0
        rets

; Consume an actual omission/angle-change notification before aging the six
; private counters. An interrupt after the clear leaves a new notification.
; Bit 7 owns the counters only after initialization; IRQs never clear it.
misfire:
        movb RL4,0xD801
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,misfire_stock
        movb RL4,0xD802
        andb RL4,#0x10
        jmpr cc_EQ,check_current
        movb RL4,#0xEF
        andb 0xD802,RL4
        mov r4,#0x0202
        mov 0xD80E,r4
        mov 0xD810,r4
        mov 0xD812,r4
        movb RL4,#0x80
        orb 0xD802,RL4
        jmpr cc_UC,inhibit
check_current:
        calls 0x3E3A2
        jmpr cc_NE,inhibit
        movb RL4,0xD802
        andb RL4,#0x80
        jmpr cc_EQ,misfire_stock
        cmp r9,#6
        jmpr cc_NC,misfire_stock
        movb RL4,[r9+#0xD80E]
        jmpr cc_EQ,misfire_stock
        subb RL4,#1
        movb [r9+#0xD80E],RL4
inhibit:
        ; Give native cylinder shutdown and its own recovery precedence.
        movb RL4,[r9+#0xABD4]
        movbz r4,RL4
        and r4,0xFD60
        jmpr cc_NE,misfire_stock
        movb RL4,[r9+#0xF53A]
        jmpr cc_NE,misfire_stock
        movb RL4,[r9+#0xABDA]
        andb 0xF55C,RL4
        jmps 0x30330
misfire_stock:
        movb RL4,[r9+#0xABD4]
        jmps 0x30222

; A delayed cylinder sample or six-segment observation can still refer to
; the preceding sequence. Resume after two visits to every cylinder.
history_pending:
        movb RL4,0xD801
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,clear
        calls 0x3E3A2
        jmpr cc_NE,done
        movb RL4,0xD802
        andb RL4,#0x10
        jmpr cc_NE,done
        movb RL4,0xD802
        andb RL4,#0x80
        jmpr cc_EQ,done
        mov r4,0xD80E
        or r4,0xD810
        or r4,0xD812
        rets
count:
        calls history_pending
        jmpr cc_NE,count_blocked
        movbz r4,0xF55A
        jmps 0x3046C
count_blocked:
        jmps 0x304DA
segment:
        calls history_pending
        jmpr cc_NE,segment_blocked
        movb RL4,0xF55C
        jmps 0x304F6
segment_blocked:
        jmps 0x306CE
