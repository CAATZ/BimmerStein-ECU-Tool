; MS41.3 Launch Control V7 soft-limiter enforcement cave.
base 0x3E100

        push DPP0
        push r4
        mov  DPP0,#4
        movb RL4,0xFDB6
        andb RL4,#0x40
        jmpr cc_EQ,done
        movb RL4,0x47E1               ; LC_CUTTYPE
        cmpb RL4,#0
        jmpr cc_NE,done
        movb RL4,0x47E3               ; LC_MAXRPM
        cmpb RL4,0xF014
        jmpr cc_NC,done
        movb 0xF014,RL4
done:   pop  r4
        pop  DPP0
        bclr 0xFD5A.15                ; displaced stock instructions
        bclr 0xFD22.15
        jmps 0x0207D6
