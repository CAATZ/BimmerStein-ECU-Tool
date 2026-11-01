; ignition_cut V16 ms411: runtime RAM D801-D804.
; Derived from ignition_cut_v11_ms411_observation.asm; only state operands are relocated.
; ms411 accepted-sample publication guard, owned by Ignition Cut alone.
; Called with IEN cleared for the short check/clear transaction. No launch call.
; A published C3 remains conservatively blocked until the timing owner refreshes it.
base 0x3F850
        calls 0x3FBE0        ; cut_active retains native cut freshness
        jmpr cc_NE,done
        movb RL4,0xD801
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,clear
        movb RL4,0xD803
        cmpb RL4,#0xC3
        jmpr cc_NE,clear
        movb RL4,#15
        orb  0xD802,RL4
done:
        rets
clear:
        mov r4,#0
        rets
