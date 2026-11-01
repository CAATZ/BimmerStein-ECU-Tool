; V18 marks knock observations dirty at the actual omitted charge transaction.
base 0x3DC70
        push r4
        movb RL4,0xD801
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,stock
        calls 0x3E660              ; sole standalone request owner
        movb RL4,0xD801
        andb RL4,#0x03
        jmpr cc_NE,cut
stock:  pop r4
        andb 0xFF04,RL1            ; displaced native coil-charge transaction
        rets
cut:    calls 0x3F000
    movb RL4,#0x1F             ; retain even a cut/release between foreground tasks
        orb 0xD802,RL4             ; four sample bits + existing misfire recovery marker
        pop r4
        rets

        nop
        nop
        nop
        nop
        nop
        nop

; CPU 3DCB0, called only by the serialized late IPW hook. R5 is caller-saved.
; Publish the valid request marker last, after its observation state is ready.
initialize:
        movb RL5,0xD801
        andb RL5,#0xF0
        cmpb RL5,#0xA0
        jmpr cc_EQ,initialized
        mov r5,#0xC000
        mov 0xD802,r5              ; freshness=0, timing=C0
        movb 0xD804,RL5            ; no timing source before marker
        calls 0x3F000
        movb RL5,#0xA0
        movb 0xD80C,RL5            ; V18 fixed-fuel status starts native
        movb 0xD801,RL5
initialized:
        rets
