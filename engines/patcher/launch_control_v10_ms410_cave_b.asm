; MS41.0 Launch Control V10: lower soft/hard threshold anchors native fuel recovery.
base 0x32C80

        push DPP0
        push r4
        mov  DPP0,#4
        jnb  0xFD80.6,done
        movb RL4,0x3021               ; LC_CUTTYPE
        cmpb RL4,#0
        jmpr cc_NE,done
        movb RL4,0x3023               ; LC_MAXRPM
        cmpb RL4,0x3027               ; explicit hard below soft wins; FF auto cannot lower
        jmpr cc_ULE,compare_stock
        movb RL4,0x3027
compare_stock:
        cmpb RL4,0xED52
        jmpr cc_NC,done
        movb 0xED52,RL4
done:   pop  r4
        pop  DPP0
        jnb  0xFD30.4,stock_skip
        jmps 0x020714
stock_skip:
        jmps 0x020726
