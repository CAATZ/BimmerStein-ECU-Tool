; Ignition Cut V9 - MS41.0 cut-active adaptation and diagnostic guards.
;
; MS41.0 has per-cylinder roughness/misfire detection in the same routine as
; its coil/resistor diagnostics. This guard enters before the detector and
; takes the stock cleanup after the diagnostic calls, suppressing both during
; an intentional cut. DTC descriptors A6E2-A732 resolve to coil codes
; 29/31/30/3/1/2 and A742 to feedback-resistor code 56; the stock table has no
; dedicated per-cylinder DTC 238-243 descriptors. Calibration controls are at
; 0x168/0x169, not the MS41.1 addresses. No grounded rear-O2 or catalyst path
; was found; heater-control code is untouched.
base 0x32D20

stft:
        push r4
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,stft_stock
        movb RL4,0xE847
        andb RL4,#0x07
        jmpr cc_EQ,stft_stock
        pop  r4
        rets
stft_stock:
        pop  r4
        push r13
        push r8
        jmps 0x28668

ltft:
        push r4
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,ltft_stock
        movb RL4,0xE847
        andb RL4,#0x07
        jmpr cc_EQ,ltft_stock
        pop  r4
        rets
ltft_stock:
        pop  r4
        mov  [r6+#0x18],r8
        rets

additive:
        push r4
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,additive_stock
        movb RL4,0xE847
        andb RL4,#0x07
        jmpr cc_EQ,additive_stock
        pop  r4
        rets
additive_stock:
        pop  r4
        mov  [r7+#0x10],r4
        rets

coil:
        push r4
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,coil_stock
        movb RL4,0xE847
        andb RL4,#0x07
        jmpr cc_EQ,coil_stock
        pop  r4
        jmps 0x2CF58
coil_stock:
        pop  r4
        movb RL4,0xFADE
        jmps 0x2CC9C

lambda:
        push r4
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,lambda_stock
        movb RL4,0xE847
        andb RL4,#0x07
        jmpr cc_EQ,lambda_stock
        pop  r4
        jmps 0x27DFA
lambda_stock:
        pop  r4
        movb RL4,[r9+#0x33]
        jmps 0x27D46

o2_front:
        push r4
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,o2_front_stock
        movb RL4,0xE847
        andb RL4,#0x07
        jmpr cc_EQ,o2_front_stock
        pop  r4
        jmps 0x279C0
o2_front_stock:
        pop  r4
        mov  r4,#0xED90
        jmps 0x27988
