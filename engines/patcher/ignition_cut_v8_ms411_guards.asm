; Ignition Cut V8 - MS41.1 cut-active adaptation and diagnostic guards.
;
; The DTC definition switches identify the front/rear O2 voltage, catalyst,
; misfire, and coil families. Their stock disabled cleanup paths are used;
; heater code is untouched.
base 0x3FBE0

stft:
        push r4
        movb RL4,0xE812
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,stft_stock
        movb RL4,0xE812
        andb RL4,#0x07
        jmpr cc_EQ,stft_stock
        pop  r4
        rets
stft_stock:
        pop  r4
        push r13
        push r8
        jmps 0x2CA5E

ltft:
        push r4
        movb RL4,0xE812
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,ltft_stock
        movb RL4,0xE812
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
        movb RL4,0xE812
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,additive_stock
        movb RL4,0xE812
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
        movb RL4,0xE812
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,coil_stock
        movb RL4,0xE812
        andb RL4,#0x07
        jmpr cc_EQ,coil_stock
        pop  r4
        jmps 0x372A6
coil_stock:
        pop  r4
        movb RL5,0xFC34
        jmps 0x36FBA

lambda:
        push r4
        movb RL4,0xE812
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,lambda_stock
        movb RL4,0xE812
        andb RL4,#0x07
        jmpr cc_EQ,lambda_stock
        pop  r4
        jmps 0x2C132
lambda_stock:
        pop  r4
        movb RL4,[r9+#0x33]
        jmps 0x2C034

o2_front:
        push r4
        movb RL4,0xE812
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,o2_front_stock
        movb RL4,0xE812
        andb RL4,#0x07
        jmpr cc_EQ,o2_front_stock
        pop  r4
        jmps 0x2B48C
o2_front_stock:
        pop  r4
        mov  r4,#0xF0EC
        jmps 0x2B430

o2_rear:
        push r4
        movb RL4,0xE812
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,o2_rear_stock
        movb RL4,0xE812
        andb RL4,#0x07
        jmpr cc_EQ,o2_rear_stock
        pop  r4
        jmps 0x2B54E
o2_rear_stock:
        pop  r4
        mov  r4,#0xF030
        jmps 0x2B4F2

misfire:
        push r4
        movb RL4,0xE812
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,misfire_stock
        movb RL4,0xE812
        andb RL4,#0x07
        jmpr cc_EQ,misfire_stock
        pop  r4
        jmps 0x3114A
misfire_stock:
        pop  r4
        movb RL4,0xFC3C
        jmps 0x30FC2

cat_bank_1:
        push r4
        movb RL4,0xE812
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,cat_bank_1_stock
        movb RL4,0xE812
        andb RL4,#0x07
        jmpr cc_EQ,cat_bank_1_stock
        pop  r4
        jmps 0x37DA6
cat_bank_1_stock:
        pop  r4
        mov  r12,#0xEE46
        jmps 0x37D54

cat_bank_2:
        push r4
        movb RL4,0xE812
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,cat_bank_2_stock
        movb RL4,0xE812
        andb RL4,#0x07
        jmpr cc_EQ,cat_bank_2_stock
        pop  r4
        jmps 0x37DA6
cat_bank_2_stock:
        pop  r4
        mov  r12,#0xEE52
        jmps 0x37D80
