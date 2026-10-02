; MS41.3 Launch Control V10: lower soft/hard threshold anchors native fuel recovery.
base 0x3E100

        push DPP0
        push r4
        mov  DPP0,#4
        jnb  0xFDB6.6,done
        movb RL4,0x47E1               ; LC_CUTTYPE
        cmpb RL4,#0
        jmpr cc_NE,done
        movb RL4,0x47E3               ; LC_MAXRPM
        cmpb RL4,0x47E7               ; explicit hard below soft wins; FF auto cannot lower
        jmpr cc_ULE,compare_stock
        movb RL4,0x47E7
compare_stock:
        cmpb RL4,0xF014
        jmpr cc_NC,done
        movb 0xF014,RL4
done:   pop  r4
        pop  DPP0
        bclr 0xFD5A.15                ; displaced stock instructions
        bclr 0xFD22.15
        jmps 0x0207D6
