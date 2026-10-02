; MS41.3 Launch Control V9: launch hard threshold cannot exceed native hard.
; Preserve the native final CMPB flags and saved DPP0/r5 contract.
base 0x3E140

        push DPP0
        push r5
        mov  DPP0,#4

        movb RL5,0xFDB6
        andb RL5,#0x40
        jmpr cc_EQ,stock
        movb RL5,0x47E1               ; LC_CUTTYPE
        cmpb RL5,#0
        jmpr cc_NE,stock

        movb RL5,0x47E7               ; LC_HARDRPM
        cmpb RL5,#0xFF
        jmpr cc_NE,validate

        movb RL5,0x47E3               ; fallback: LC_MAXRPM + 3 raw
        cmpb RL5,#0xFD
        jmpr cc_C,fallback_add
        movb RL5,#0xFF
        jmpr cc_UC,compare
fallback_add:
        addb RL5,#3
        jmpr cc_UC,compare

validate:
        cmpb RL5,0x47E3
        jmpr cc_NC,compare
        movb RL5,0x47E3

compare:
        ; The native hard limit also bounds explicit, clamped, and auto launch limits.
        cmpb RL5,0xDB87
        jmpr cc_NC,stock
        cmpb RL4,RL5
        jmpr cc_UC,done

stock:  cmpb RL4,0xDB87
done:   pop  r5
        pop  DPP0
        rets
