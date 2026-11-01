; V17: enforce the requested late angle without advancing native protected timing.
; Both native event callers supply a saturated inverse angle in R2 (0..255).
; Larger inverse angle is later; R0 is the displaced instruction's output.
base 0x32EC0
        push r4
        calls 0x33040
        cmpb RL4,#63
        jmpr cc_UGT,stock_split
        mov r0,#255
        subb RL0,RL4
        cmp r2,r0
        jmpr cc_NC,protected_angle
        mov r2,r0
protected_angle:
        calls 0x33600
        movb RL4,#3
        orb 0xD802,RL4
stock_split:
        pop r4
        mov r0,r2
        andb RL0,#0x0F
        rets
