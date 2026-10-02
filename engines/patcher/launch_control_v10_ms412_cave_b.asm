; MS41.2 Launch Control V10: lower soft/hard threshold anchors native fuel recovery.
base 0x3E100

        push DPP0
        push r4
        mov  DPP0,#4
        jnb  0xFDB6.6,done
        movb RL4,0x352D               ; LC_CUTTYPE
        cmpb RL4,#0
        jmpr cc_NE,done
        movb RL4,0x352F               ; LC_MAXRPM
        cmpb RL4,0x3533               ; explicit hard below soft wins; FF auto cannot lower
        jmpr cc_ULE,compare_stock
        movb RL4,0x3533
compare_stock:
        cmpb RL4,0xF014
        jmpr cc_NC,done
        movb 0xF014,RL4
done:   pop  r4
        pop  DPP0
        jnb  0xFD30.4,stock_skip
        jmps 0x0207D6
stock_skip:
        jmps 0x0207E8
