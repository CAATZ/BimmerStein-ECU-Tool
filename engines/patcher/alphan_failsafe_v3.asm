; AlphaN MAF Failsafe V3 - SS1v2-native load-domain integration.
;
; File 0x2A86A hooks the native mode gate after the untouched Speed Density
; branch. An active DTC8 flag enters SS1v2's existing expanded AlphaN producer;
; manual AlphaN retains its native DTC12 guard and healthy MAF skips to the
; shared publisher. File 0x2A8B4 converts the legacy DTC12 table byte to the
; reduced pre-x4 domain only when DTC8 forced the producer past that guard.
; The last two entries restore the stock MS41.2 effective condition
; (manual AlphaN OR active DTC8) around SS1v2's downstream load consumers.
base 0x3DB6A

mode_gate:
        jb   0xFD30.1,maf_fault
        jnb  0xFD22.10,maf_mode
        jmps 0x02E86E
maf_fault:
        jmps 0x02E872
maf_mode:
        calls 0x02B664
        jmps 0x02E8EE

dtc12_tps:
        jb   0xFD30.0,dtc12_table
        mov  r12,#0x4048
        jmps 0x02E8B8
dtc12_table:
        mov  r12,#0x04BE
        calls 0x034A34
        movbz r4,RL4
        shr  r4,#2
        movb RL6,RL4
        jmps 0x02E8BE

guard_fc52:
        jb   0xFD22.10,guard_fc52_taken
        jb   0xFD30.1,guard_fc52_taken
        jmps 0x038F64
guard_fc52_taken:
        jmps 0x038F82

guard_e8e4:
        jb   0xFD22.10,guard_e8e4_taken
        jb   0xFD30.1,guard_e8e4_taken
        jmps 0x03905E
guard_e8e4_taken:
        jmps 0x03907C
