; ignition_cut V16 ms412: runtime RAM D801-D804.
; Derived from ignition_cut_v9_ms4123_gate.asm; only state operands are relocated.
; Ignition Cut V9 - shared MS41.2/MS41.3 final-stage gate.
;
; The late IPW/control hook owns the standalone request in D801.0.
; Launch Control owns its ignition request in D801.1.  A0 in the high nibble
; marks the scratch byte initialized.  This ISR only consumes those states:
; invalid/uninitialized state fails stock, either request suppresses the
; displaced six-channel coil-charge transaction.
base 0x3DC70

        push r4
        movb RL4,0xD801
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,stock

        movb RL4,0xD801
        andb RL4,#0x03
        jmpr cc_NE,cut

stock:  pop  r4
        andb 0xFF04,RL1
        rets

cut:    pop  r4
        rets
