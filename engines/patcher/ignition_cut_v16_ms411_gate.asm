; ignition_cut V16 ms411: runtime RAM D801-D804.
; Derived from ignition_cut_v9_ms411_gate.asm; only state operands are relocated.
; Ignition Cut V9 - MS41.1 final-stage gate.
base 0x3F680

        push r4
        movb RL4,0xD801
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,stock

        movb RL4,0xD801
        andb RL4,#0x03
        jmpr cc_NE,cut

stock:  pop  r4
        andb 0xFF04,RL1
        rets

cut:    pop  r4
        rets
