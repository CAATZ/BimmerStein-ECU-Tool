; Launch Control V4 - independently calibrated hard limiter comparator.
;
; Called in place of both native `cmpb RL4,0xDB87` instructions at file
; 0x2488A and 0x2492A. RL4 is actual RPM/32. In active launch fuel mode,
; compare against LC_HARDRPM instead of the selected stock DB87 threshold.
;
; LC_HARDRPM=0xFF is the backward-safe/unconfigured value: use
; LC_MAXRPM+3 (96 RPM), saturating at 0xFF. A configured hard threshold below
; LC_MAXRPM is clamped to LC_MAXRPM. The final CMPB flags survive POP/RETS and
; feed the untouched stock branches.
base 0x3DE20

        push DPP0
        push r5
        mov  DPP0,#4

        movb RL5,0xFD5A
        andb RL5,#0x40                ; launch latch fd5a.6
        jmpr cc_EQ,stock
        movb RL5,0x352D               ; LC_CUTTYPE: 0=fuel, 1=ignition
        cmpb RL5,#0
        jmpr cc_NE,stock

        movb RL5,0x3533               ; LC_HARDRPM
        cmpb RL5,#0xFF
        jmpr cc_NE,validate

        movb RL5,0x352F               ; fallback: LC_MAXRPM + 3 raw
        cmpb RL5,#0xFD
        jmpr cc_C,fallback_add
        movb RL5,#0xFF                ; saturate instead of wrapping
        jmpr cc_UC,compare
fallback_add:
        addb RL5,#3
        jmpr cc_UC,compare

validate:
        cmpb RL5,0x352F               ; hard threshold must be >= soft
        jmpr cc_NC,compare
        movb RL5,0x352F               ; fail safe: clamp hard to soft

compare:
        cmpb RL4,RL5
        jmpr cc_UC,done

stock:  cmpb RL4,0xDB87
done:   pop  r5
        pop  DPP0
        rets
