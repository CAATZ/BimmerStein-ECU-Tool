; V18: enforce the requested late angle without advancing native protected timing.
; Knock invalidation starts only when R2 actually changes; ties preserve native timing.
; Both native event callers supply a saturated inverse angle in R2 (0..255).
; Larger inverse angle is later; R0 is the displaced instruction's output.
base 0x3E700
        push r4
        calls 0x3E800
        cmpb RL4,#63
        jmpr cc_UGT,release_episode
        mov r0,#255
        subb RL0,RL4
        cmp r2,r0
        jmpr cc_NC,protected_angle
        mov r2,r0
        calls 0x3F036
protected_angle:
        movb RL4,#31
        orb 0xD802,RL4
        jmpr cc_UC,stock_split
release_episode:
        calls 0x3F04E
stock_split:
        pop r4
        mov r0,r2
        andb RL0,#0x0F
        rets
