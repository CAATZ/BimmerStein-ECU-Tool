; Ignition Cut V7 - six-channel final-stage coil-charge gate.
;
; Shared unchanged by MS41.2 and MS41.3:
;   file 0x3992A / CPU 0x3D92A: andb P1L,RL1
;   file 0x3998E / CPU 0x3D98E: andb P1L,RL1
;
; The CC6 interrupt is configured as timer/interrupt-only Compare Mode 0. Its
; ISR selects a stock mask and executes the ANDB above. The normal ADB2 table
; clears one P1L.0..5 cylinder output plus a companion P1L.6/.7 bit; the fd5e.8
; ADC4 table clears paired cylinder outputs plus a companion bit. The paired CC7
; ISR later ORs the matching ignition mask into P1L to release the final stage.
; Startup initializes P1L/DP1L high/output, and the independent over-dwell
; watchdog monitors the six low-active P1L.0..5 ignition outputs.
;
; Both native ANDB sites CALL this shared subroutine. The stock branch restores
; context and replays the displaced ANDB. The cut branch restores context and
; returns without applying that complete stock mask, so the scheduled coil or
; paired-coil charge transaction never begins.
; It deliberately does not force P1L high: an unexpected mid-charge invocation
; must not release a charged coil early.
base 0x3DC70

        push DPP0
        push r4
        mov  DPP0,#4

        ; Launch Control already applied its own RPM threshold before it
        ; raises fd5a.7. This request deliberately bypasses CUTSW/CUTRPM.
        movb RL4,0xFD5A
        andb RL4,#0x80
        jmpr cc_NE,cut

        movb RL4,0x2A65              ; CUTSW: FF off, 00 always, 1/2/4 pins
        cmpb RL4,#0xFF
        jmpr cc_EQ,stock
        cmpb RL4,#0
        jmpr cc_EQ,rpm_gate

        cmpb RL4,#1
        jmpr cc_NE,pin81
        movb RL4,0xFD61              ; SIR selector 01: P1.12 / pin 80 / fd60.9
        andb RL4,#0x02
        jmpr cc_EQ,stock
        jmpr cc_UC,rpm_gate
pin81:  cmpb RL4,#2
        jmpr cc_NE,pin82
        movb RL4,0xFD61              ; SIR selector 02: P1.13 / pin 81 / fd60.8
        andb RL4,#1
        jmpr cc_EQ,stock
        jmpr cc_UC,rpm_gate
pin82:  cmpb RL4,#4
        jmpr cc_NE,stock
        movb RL4,0xFD60              ; SIR selector 04: P1.14 / pin 82 / fd60.7
        andb RL4,#0x80
        jmpr cc_EQ,stock

rpm_gate:
        movb RL4,0xFC3C              ; actual engine speed, RPM/32
        cmpb RL4,0x2A66              ; CUTRPM
        jmpr cc_C,stock

cut:    pop  r4
        pop  DPP0
        rets                         ; suppress only this coil-charge start

stock:  pop  r4
        pop  DPP0
        andb 0xFF04,RL1              ; displaced per-cylinder P1L clear
        rets
