; MS41.1 Launch Control V10: lower soft/hard threshold anchors native fuel recovery.
base 0x3FB40

        push DPP0
        push r4
        mov  DPP0,#4
        jnb  0xFDB6.6,done
        movb RL4,0x3711               ; LC_CUTTYPE
        cmpb RL4,#0
        jmpr cc_NE,done
        movb RL4,0x3713               ; LC_MAXRPM
        cmpb RL4,0x3717               ; explicit hard below soft wins; FF auto cannot lower
        jmpr cc_ULE,compare_stock
        movb RL4,0x3717
compare_stock:
        cmpb RL4,0xF02C
        jmpr cc_NC,done
        movb 0xF02C,RL4
done:   pop  r4
        pop  DPP0
        jnb  0xFD30.4,stock_skip
        jmps 0x0207DA
stock_skip:
        jmps 0x0207EC
