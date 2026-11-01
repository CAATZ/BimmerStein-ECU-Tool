; ms410 accepted-sample publication guard, owned by Ignition Cut alone.
; Called with IEN cleared for the short check/clear transaction. No launch call.
; A published C3 remains conservatively blocked until the timing owner refreshes it.
base 0x32F50
        calls 0x32D20        ; cut_active retains native cut freshness
        jmpr cc_NE,done
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,clear
        movb RL4,0xE849
        cmpb RL4,#0xC3
        jmpr cc_NE,clear
        movb RL4,#3
        orb  0xE848,RL4
done:
        rets
clear:
        mov r4,#0
        rets
