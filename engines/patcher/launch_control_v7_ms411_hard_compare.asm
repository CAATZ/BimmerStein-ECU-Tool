; MS41.1 Launch Control V7 independently calibrated hard comparator.
base 0x3FB80

        push DPP0
        push r5
        mov  DPP0,#4

        movb RL5,0xFDB6
        andb RL5,#0x40
        jmpr cc_EQ,stock
        movb RL5,0x3711               ; LC_CUTTYPE
        cmpb RL5,#0
        jmpr cc_NE,stock

        movb RL5,0x3717               ; LC_HARDRPM
        cmpb RL5,#0xFF
        jmpr cc_NE,validate

        movb RL5,0x3713               ; fallback: LC_MAXRPM + 3 raw
        cmpb RL5,#0xFD
        jmpr cc_C,fallback_add
        movb RL5,#0xFF
        jmpr cc_UC,compare
fallback_add:
        addb RL5,#3
        jmpr cc_UC,compare

validate:
        cmpb RL5,0x3713
        jmpr cc_NC,compare
        movb RL5,0x3713

compare:
        cmpb RL4,RL5
        jmpr cc_UC,done

stock:  cmpb RL4,0xDB02
done:   pop  r5
        pop  DPP0
        rets
