base 0x3e600
foreground:
        push PSW
        bclr 0xFF10.11
        push r5
        movb RL5,0xD801
        andb RL5,#0xF0
        cmpb RL5,#0xA0
        jmpr cc_EQ,initialized
        mov r5,#0xC000
        mov 0xD802,r5
        movb 0xD804,RL5
        movb 0xD80D,RL5            ; no inherited RPM stage at initialization
        movb RL5,#0xA0
        movb 0xD80C,RL5
        movb 0xD801,RL5
initialized:
        pop r5
        pop PSW
        mov r4,0xef7c
        rets
