; Ignition Cut V9 - MS41.3 cut-active adaptation and diagnostic guards.
;
; E847 must carry the initialized A0 marker and at least one real cut request.
; SS1v2 has no MS41.2-style 0x37B lambda-regulation DTC routine; its separate
; upstream/downstream voltage, catalyst, coil, and misfire paths are guarded.
base 0x3E1A0

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
        jmps 0x2BF14

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
        jmps 0x3582E
coil_stock:
        pop  r4
        movb RL5,0xFC34
        jmps 0x35542

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
        jmps 0x2B0BC
o2_front_stock:
        pop  r4
        mov  r4,#0xF0C4
        jmps 0x2B060

o2_rear:
        push r4
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,o2_rear_stock
        movb RL4,0xE847
        andb RL4,#0x07
        jmpr cc_EQ,o2_rear_stock
        pop  r4
        jmps 0x2B17E
o2_rear_stock:
        pop  r4
        mov  r4,#0xF018
        jmps 0x2B122

cat_bank_1:
        push r4
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,cat_bank_1_stock
        movb RL4,0xE847
        andb RL4,#0x07
        jmpr cc_EQ,cat_bank_1_stock
        pop  r4
        jmps 0x3629E
cat_bank_1_stock:
        pop  r4
        mov  r12,#0xEE46
        jmps 0x3624C

cat_bank_2:
        push r4
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,cat_bank_2_stock
        movb RL4,0xE847
        andb RL4,#0x07
        jmpr cc_EQ,cat_bank_2_stock
        pop  r4
        jmps 0x3629E
cat_bank_2_stock:
        pop  r4
        mov  r12,#0xEE52
        jmps 0x36278

misfire:
        push r4
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,misfire_stock
        movb RL4,0xE847
        andb RL4,#0x07
        jmpr cc_EQ,misfire_stock
        pop  r4
        jmps 0x30330
misfire_stock:
        pop  r4
        movb RL4,0xFC3C
        jmps 0x30260
