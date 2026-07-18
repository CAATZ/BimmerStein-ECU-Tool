; MS41.2 Launch Control V4 soft-limiter enforcement cave.
;
; Entry: file 0x39DBC / CPU 0x3DDBC from file 0x247D2. Preserve the two
; native fd30.4 continuations after applying the non-persistent F014 clamp.
base 0x3DDBC

        push DPP0
        push r4
        mov  DPP0,#4
        movb RL4,0xFD5A
        andb RL4,#0x40
        jmpr cc_EQ,done
        movb RL4,0x352D               ; LC_CUTTYPE
        cmpb RL4,#0
        jmpr cc_NE,done
        movb RL4,0x352F               ; LC_MAXRPM = launch soft limit
        cmpb RL4,0xF014
        jmpr cc_NC,done
        movb 0xF014,RL4
done:   pop  r4
        pop  DPP0
        jnb  0xFD30.4,stock_skip
        jmps 0x0207D6
stock_skip:
        jmps 0x0207E8
