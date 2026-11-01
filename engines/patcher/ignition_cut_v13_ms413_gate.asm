; Ignition Cut V13 - MS41.3 event-time standalone decision.
; Both native CC6 charge sites call this gate. Launch owns E847.1.
; Request evaluation preserves the native mask and all scratch registers.
base 0x3DC70
        push r4
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,stock
        calls 0x3E660              ; sole standalone request owner
        movb RL4,0xE847
        andb RL4,#0x03
        jmpr cc_NE,cut
stock:  pop r4
        andb 0xFF04,RL1            ; displaced native coil-charge transaction
        rets
cut:    movb RL4,#0x1F             ; retain even a cut/release between foreground tasks
        orb 0xE848,RL4             ; four sample bits + existing misfire recovery marker
        pop r4
        rets

        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop

; CPU 3DCB0, called only by the serialized late IPW hook. R5 is caller-saved.
; Publish the valid request marker last, after its observation state is ready.
initialize:
        movb RL5,0xE847
        andb RL5,#0xF0
        cmpb RL5,#0xA0
        jmpr cc_EQ,initialized
        movb RL5,#0
        movb 0xE848,RL5
        movb RL5,#0xC0
        movb 0xE849,RL5
        movb RL5,#0xA0
        movb 0xE847,RL5
initialized:
        rets
