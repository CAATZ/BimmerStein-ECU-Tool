; ms412 accepted-sample publication guard, owned by Ignition Cut alone.
; Called with IEN cleared for the short check/clear transaction. No launch call.
; A published C3 remains conservatively blocked until the timing owner refreshes it.
base 0x3E790
        calls 0x3E1A0        ; cut_active retains native cut freshness
        jmpr cc_NE,done
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,clear
        movb RL4,0xE849
        cmpb RL4,#0xC3
        jmpr cc_NE,clear
        movb RL4,#15
        orb  0xE848,RL4
done:
        rets
clear:
        mov r4,#0
        rets

; Ig-owned pending recovery entry at +0x2A; never calls an absent Launch helper.
; Only native misfire owners consume bit 4; unknown startup RAM is not recovery.
misfire_pending:
        calls 0x3E3A2            ; combustion_active
        jmpr cc_NE,pending_done
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,clear
        movb RL4,0xE848
        andb RL4,#0x10
pending_done:
        rets
