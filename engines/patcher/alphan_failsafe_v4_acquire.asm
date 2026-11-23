; SS1v2 AlphaN V4 acquisition hooks. Startup RAM clear owns initialization.
; D814/16 hold the latest clean raw MAF load and airflow. D818/19 mark
; individual native ADC banks containing MAP. D81A/1C preserve pre-SD
; diagnostic history. D81D holds analog diagnosis across a source transition.
base 0x3DB6A
capture_load:
        mov  0xFC52,0xFE0E
        ; Native handover has already switched the ADC to its other bank.
        jb   0xFD02.4,consumed_a
        movb RL1,0xD819
        movb 0xD819,0xFF1C
        jmpr cc_UC,quality
consumed_a:
        movb RL1,0xD818
        movb 0xD818,0xFF1C
quality:
        cmpb RL1,#0
        jmpr cc_NE,capture_done
        jb   0xFD22.9,capture_done
        cmp  r2,#0
        jmpr cc_EQ,capture_done
        mov  0xD814,0xFE0E
        mov  0xD816,r5
        movb 0xD81D,0xFF1C
capture_done:
        jmps 0x034EF6

zero_speed:
        mov  0xFC52,r5
        mov  0xD814,r5
        mov  0xD816,r5
        jmps 0x034E96
no_sync:
        mov  0xFC54,0xFF1C
        mov  0xD814,0xFF1C
        mov  0xD816,0xFF1C
        jmps 0x028118

map_sample:
        ; Called only on the MAP arm of the stock ADC selector. Mark this
        ; bank so an acquisition spanning a mode change cannot certify MAF.
        movb RL4,#1
        movb 0xD81D,RL4
        jb   0xFD02.4,map_bank_b
        movb 0xD818,RL4
        jmpr cc_UC,convert_map
map_bank_b:
        movb 0xD819,RL4
convert_map:
        mov  r4,#0
        calls 0x020740
        rets

; The native monitor tests analog-high before either digital qualifier.
; While the source changes, bypass that one analog test and let both
; native digital paths run with all their original permissions. If neither
; digital path fires, bypass analog-low and recovery until MAF is valid.
; The aligned word at D81C contains both source-transition flags.
monitor_high:
        jb   0xFD22.9,high_native
        cmp  0xFF1C,0xD81C
        jmpr cc_NE,digital_qualifiers
high_native:
        movb RL4,0xE8E9
        jmps 0x02E5C6
digital_qualifiers:
        jmps 0x02E614
monitor_low_one:
        jb   0xFD22.9,low_one_native
        cmp  0xFF1C,0xD81C
        jmpr cc_NE,monitor_skip
low_one_native:
        movb RL5,0xE8E9
        jmps 0x02E6B4
monitor_low_two:
        jb   0xFD22.9,low_two_native
        cmp  0xFF1C,0xD81C
        jmpr cc_NE,monitor_skip
low_two_native:
        movb RL5,0xE8E9
        jmps 0x02E7C0
monitor_skip:
        jmps 0x02E854
