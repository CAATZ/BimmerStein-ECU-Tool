; V19 accepted-sample guard. Keep the native recovery entry offsets.
base 0x3E790
        calls 0x3E1A0        ; cut_active retains native cut freshness
        jmpr cc_NE,done
        movb RL4,0xD801
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,clear
        calls 0x3e3d2
        jmpr cc_NE,clear
        movb RL4,#15
        orb  0xD802,RL4
done:
        rets
        nop
        nop
clear:
        mov r4,#0
        rets

; Ig-owned pending recovery entry at +0x2A; never calls an absent Launch helper.
; Only native misfire owners consume bit 4; unknown startup RAM is not recovery.
misfire_pending:
        calls 0x3E3A2            ; combustion_active
        jmpr cc_NE,pending_done
        movb RL4,0xD801
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,clear
        movb RL4,0xD802
        andb RL4,#0x10
pending_done:
        rets
