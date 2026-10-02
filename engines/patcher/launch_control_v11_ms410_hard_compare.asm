; MS41.0 Launch Control V11: launch hard threshold cannot exceed native hard.
; Preserve the native final CMPB flags and saved DPP0/r5 contract.
base 0x32CC0

        push DPP0
        push r5
        mov  DPP0,#4

        movb RL5,0xFD80
        andb RL5,#0x40
        jmpr cc_EQ,stock
        movb RL5,0x3021               ; LC_CUTTYPE
        cmpb RL5,#0
        jmpr cc_NE,stock

        movb RL5,0x3027               ; LC_HARDRPM
        cmpb RL5,#0xFF
        jmpr cc_NE,compare

        movb RL5,0x3023               ; fallback: LC_MAXRPM + 3 raw
        cmpb RL5,#0xFD
        jmpr cc_C,fallback_add
        movb RL5,#0xFF
        jmpr cc_UC,compare
fallback_add:
        addb RL5,#3
        jmpr cc_UC,compare

compare:
        ; The native hard limit also bounds independent explicit and auto launch limits.
        cmpb RL5,0x01D3
        jmpr cc_NC,stock
        cmpb RL4,RL5
        jmpr cc_UC,done

stock:  cmpb RL4,0x01D3
done:   pop  r5
        pop  DPP0
        rets
