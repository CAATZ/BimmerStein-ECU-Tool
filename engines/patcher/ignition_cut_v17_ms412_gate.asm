; V17 marks knock observations dirty at the actual omitted charge transaction.
base 0x3DC70

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

cut:    calls 0x3F000
    pop  r4
        rets
