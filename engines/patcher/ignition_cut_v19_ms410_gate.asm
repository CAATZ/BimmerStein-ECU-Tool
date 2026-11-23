; Record each omitted charge before returning to the native IRQ.
base 0x32820

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

cut:    calls 0x33600
        movb RL4,#3
        orb 0xD802,RL4
        pop  r4
        rets
