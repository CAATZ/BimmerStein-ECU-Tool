; SS1v2 AlphaN V4 foreground load and transient-fuel routing.
; R8 is saved for this invocation: 0 native, 1 measure before modeling,
; 2 filter the engine result while retaining measured diagnostic history.
base 0x3EC98
load_gate:
        push r8
        mov  r8,#0
        jnb  0xFD22.9,maf_source
        movb RL4,0xD81C
        jmpr cc_NE,sd_publish
        mov  r4,0xE8E8
        mov  0xD81A,r4
        movb RL4,#1
        movb 0xD81C,RL4
sd_publish:
        jmps 0x02E8EA

maf_source:
        movb RL4,0xD81C
        jmpr cc_EQ,read_sample
        mov  r4,0xD81A
        mov  0xE8E8,r4
        movb 0xD81C,0xFF1C
read_sample:
        ; Take one completed acquisition pair, including its quarantine flag.
        push PSW
        bclr 0xFF10.11
        mov  r4,0xD814
        mov  r5,0xD816
        movb RL6,0xD81D
        pop  PSW
        jb   0xFD30.1,model
        jnb  0xFD22.10,measured_engine
        jnb  0xFD30.0,model
measured_engine:
        mov  0xFC52,r4
        mov  0xFC54,r5
        cmpb RL6,#0
        jmpr cc_EQ,sd_publish
        mov  r8,#2
        jmpr cc_UC,sd_publish

model:
        cmpb RL6,#0
        jmpr cc_NE,model_only
        ; FC52 is in the reduced domain at acquisition. Give the diagnostic
        ; exactly the saturated legacy target used by the native filter.
        mov  r6,r4
        movbz r5,0x33BF
        and  r5,#3
        jmpr cc_EQ,measure
scale:
        add  r6,r6
        jmpr cc_C,saturated
        sub  r5,#1
        jmpr cc_NE,scale
        jmpr cc_UC,measure
saturated:
        mov  r6,#0xFFFF
measure:
        mov  r8,#1
        jmps 0x02E8EE
model_only:
        mov  r8,#2
        jmps 0x02E872

; The two calibration branches select a filter coefficient from load. On
; the measurement pass, use the same measured target for the axis as for
; the filter. The other branches use the stock operating-state constants.
gain_one:
        cmp  r8,#1
        jmpr cc_NE,gain_one_native
        movbz r13,RH6
        jmps 0x02E90A
gain_one_native:
        movbz r13,0xFC53
        jmps 0x02E90A
gain_two:
        cmp  r8,#1
        jmpr cc_NE,gain_two_native
        movbz r13,RH6
        jmps 0x02E986
gain_two_native:
        movbz r13,0xFC53
        jmps 0x02E986

filter_inputs:
        mov  r12,0xE8E8
        cmp  r8,#1
        jmpr cc_NE,engine_input
        mov  r13,r6
        jmps 0x02E99E
engine_input:
        cmp  r8,#2
        jmpr cc_NE,native_target
        mov  r12,r9
native_target:
        jmps 0x02E99A

filter_result:
        cmp  r8,#2
        jmpr cc_EQ,engine_store
        mov  0xE8E8,r4
        cmp  r8,#1
        jmpr cc_EQ,model_only
engine_store:
        pop  r8
        jmps 0x02E9A8

dtc12_tps:
        jb   0xFD30.0,dtc12_curve
        mov  r12,#0x4048
        jmps 0x02E8B8
dtc12_curve:
        ; SA 04BC belongs to the six-byte SA 04BE curve. The expanded
        ; producer left a 20-point RPM index in the interpolation registers.
        mov  r12,#0x04BC
        movbz r13,0xFC3C
        calls 0x0348AC
        mov  r12,#0x04BE
        calls 0x034A34
        ; Align both terms in Q7, then apply native sum saturation and IAT.
        movbz r12,RL4
        shl  r12,#7
        movbz r5,0x33BF
        and  r5,#3
        shr  r12,r5
        movbz r13,0xE8EB
        shl  r13,#7
        add  r12,r13
        cmp  r12,#0x7F80
        jmpr cc_ULE,temperature
        mov  r12,#0x7F80
temperature:
        jmps 0x02E8CC

guard_fc52:
        jb   0xFD22.10,fc52_taken
        jb   0xFD22.9,fc52_native
        jb   0xFD30.1,fc52_taken
fc52_native:
        jmps 0x038F64
fc52_taken:
        jmps 0x038F82
guard_e8e4:
        jb   0xFD22.10,e8e4_taken
        jb   0xFD22.9,e8e4_native
        jb   0xFD30.1,e8e4_taken
e8e4_native:
        jmps 0x03905E
e8e4_taken:
        jmps 0x03907C
