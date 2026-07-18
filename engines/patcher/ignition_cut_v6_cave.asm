; Ignition Cut V6 - DEPRECATED field-failed P3.14 gate.
;
; Entry: file 0x39C70 / CPU 0x3DC70 from file 0x2B518, the unique live
;     bset P3.14
;     movb RL4,#0
; pair in a recurring scheduler. On-car testing proved that skipping P3.14
; does not interrupt spark: P3.14 is not one of the six coil final stages.
; Retained byte-for-byte only so installed V6 images can be detected/reverted.
base 0x3DC70

        push DPP0
        mov  DPP0,#4

        ; Launch Control V3/V4 has already applied its RPM threshold when it
        ; raises fd5a.7.  This request deliberately bypasses CUTSW.
        jb   0xFD5A.7,cut

        movb RL4,0x2A65              ; CUTSW: FF off, 00 always, 1/2/4 pins
        cmpb RL4,#0xFF
        jmpr cc_EQ,stock
        cmpb RL4,#0
        jmpr cc_EQ,rpm_gate

        cmpb RL4,#1
        jmpr cc_NE,pin81
        movb RL4,0xFD60
        andb RL4,#0x80
        jmpr cc_EQ,stock
        jmpr cc_UC,rpm_gate
pin81:  cmpb RL4,#2
        jmpr cc_NE,pin82
        movb RL4,0xFD61
        andb RL4,#1
        jmpr cc_EQ,stock
        jmpr cc_UC,rpm_gate
pin82:  cmpb RL4,#4
        jmpr cc_NE,stock
        movb RL4,0xFD61
        andb RL4,#2
        jmpr cc_EQ,stock

rpm_gate:
        movb RL4,0xFC3C              ; actual engine speed, RPM/32
        cmpb RL4,0x2A66              ; CUTRPM
        jmpr cc_C,stock

cut:    pop  DPP0
        movb RL4,#0                  ; preserve displaced second instruction
        jmps 0x02F51C                ; file 0x2B51C, skip coil-charge BSET

stock:  pop  DPP0
        bset 0xFFC4.14               ; displaced live coil-charge start
        movb RL4,#0
        jmps 0x02F51C
