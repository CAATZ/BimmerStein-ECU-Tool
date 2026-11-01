"""Exact native fixed-IPW execution in canonical ms41emu, without device I/O.

The bound is the last synchronized segment in the native clock domain. It is
not a prediction of the next crank interval or physical injector behavior.
"""

LAYOUTS = {
    "MS41.0": dict(base=0x33A00, control=0x32A00, hook=0x25840, end=0x258D0,
        task=0x254F2, avg=0xECBA, prev=0xECC0, banks=(0xECBC,0xECBE),
        timers=(0xFACA,0xFACC), delta=0xFAD2, latency=0xF0EA, index=0xFB4A,
        flags=0xFD4E, rpm=0xFAE6, cut=0x3013, launch=0x3029),
    "MS41.1": dict(base=0x6B00, control=0x3F8C0, hook=0x27A5A, end=0x27AEA,
        task=0x276F4, avg=0xEF94, prev=0xEF9A, banks=(0xEF96,0xEF98),
        timers=(0xFC20,0xFC22), delta=0xFC28, latency=0xF684, index=0xFCA0,
        flags=0xFD5E, rpm=0xFC3C, cut=0x3703, launch=0x3719),
    "MS41.2": dict(base=0x3F400, control=0x3E600, hook=0x2755A, end=0x275EA,
        task=0x271F4, avg=0xEF7C, prev=0xEF82, banks=(0xEF7E,0xEF80),
        timers=(0xFC20,0xFC22), delta=0xFC28, latency=0xF68E, index=0xFCA0,
        flags=0xFD5E, rpm=0xFC3C, cut=0x2A68, launch=0x3535),
    "MS41.3": dict(base=0x3F400, control=0x3E600, hook=0x2755A, end=0x275EA,
        task=0x271F4, avg=0xEF7C, prev=0xEF82, banks=(0xEF7E,0xEF80),
        timers=(0xFC20,0xFC22), delta=0xFC28, latency=0xF68E, index=0xFCA0,
        flags=0xFD5E, rpm=0xFC3C, cut=0x2A68, launch=0x47E9),
}
METRICS = {}


def _tuned(gate, image, p, ipw, launch=0xFFFF):
    candidate = bytearray(image)
    for address, value in ((p["cut"],ipw),(p["launch"],launch)):
        offset = (0x10000 + address) ^ 0x4000
        candidate[offset:offset+2] = value.to_bytes(2,"little")
    return gate._bind_image(candidate, gate._bound_variant(image))


def _run(emu, start, stop, limit=12000):
    emu.watchdog.service()
    result = emu.run_from(start, stop_at=stop, max_steps=limit)
    assert result.exit_reason == "stop_at" and result.final_pc == stop, result
    return result


def _seed(gate, image, p, *, trim=128, latency=100, state=0xA1, status=0xA0,
          phase=0, cylinder=0, dpp0=4, period=40000):
    emu = gate._emu(image,dpp0=dpp0)
    emu.reg.dpp[:] = [dpp0,5,0,3]
    emu.reg.cp,emu.reg.sp = 0xFC00,0xFB80
    emu.interrupts.enabled = True
    for i in range(16):
        emu.reg.r[i] = 0x3151 + i*0x217
    emu.reg.r[0] = 0xFA40
    emu.reg.psw.unpack(0x0800)
    for address,value in ((p['avg'],400),(p['banks'][0],500),(p['banks'][1],600),
            (p['latency'],latency),(0xFA74,period),(p['flags'],0x400),
            (0xFF50,0x4340),(0xFF46,0x6041),(0xFE46,0),(0xFE52,1000),
            (0xFD02,0),(0xFD12,0)):
        emu.write(address,value)
    for address,value in ((0xD801,state),(0xD80C,status),(0xFA80,phase),
            (0xFA81,0),(p['index'],cylinder),(0xD805,0xA7),(0xD803,0xC0)):
        emu.write_byte(address,value)
    trims = [trim]*6 if isinstance(trim,int) else trim
    for i,t in enumerate(trims):
        emu.write_byte(0xDC28+i,t)
        emu.write(p['delta']+2*i,0x1234)
    return emu


def _call(emu,entry):
    before = (emu.reg.r[0],emu.reg.cp,tuple(emu.reg.dpp),emu.reg.sp)
    emu.reg.sp -= 4
    emu.write(emu.reg.sp,0xE000)
    emu.write(emu.reg.sp+2,0)
    result = _run(emu,entry,0xE000)
    assert (emu.reg.r[0],emu.reg.cp,tuple(emu.reg.dpp),emu.reg.sp) == before
    return result


def _values(emu,p):
    return ([emu.read(a) for a in p['banks']], [emu.read(a) for a in p['timers']],
            [emu.read(p['delta']+i*2) for i in range(6)])


def _corrected(base,trim):
    return min(65535,base*(0x800+16*trim)//0x1000)


def _consumer(emu,p,version,stage=0,mask=0,transient=0,inhibit=False):
    """Exact bounded native six-output publisher, explicit timer/context."""
    older = version=='MS41.0'
    start,end = (0x592E,0x5A6A) if older else (0x59B0,0x5AEC)
    emu.reg.cp = 0xFB20
    emu.reg.r[0],emu.reg.r[1],emu.reg.r[3] = 0x3F00,0x0469,0x3F00
    emu.write(0xFF50,0)  # Freeze T1 only for the exact interval arithmetic check.
    emu.write(0xFF46,0)
    emu.write(0xFE52,1000)
    emu.write_byte(0xFADE if older else 0xFC34,stage)
    emu.write_byte(0xFADF if older else 0xFC35,mask)
    emu.write_byte(0xFABE,transient)
    emu.write(p['flags'],0x100 if inhibit else 0)
    emu.write(0xFFC0,0xFFFF)
    emu.write(0xFFC2,63)
    for i in range(6):
        emu.write(0xFE80+2*i,0xDEAD)
    _run(emu,start,end)
    return [emu.read(0xFE80+2*i) for i in range(6)]


def _preemption(gate,version,image,p):
    """Canonical IRQ delivery at every helper boundary; ISA observer only.

    The test vector and short RAM observer are declared instrumentation. They
    read exactly the timer-bank plus per-channel delta consumed by native T4.
    Native helpers and their arithmetic are never replaced or simulated.
    """
    candidate = bytearray(image)
    candidate[0x40A0:0x40A4] = bytes.fromhex('FA0000E1')
    candidate = gate._bind_image(candidate,gate._bound_variant(image))
    stub = bytearray.fromhex('ECF4')  # PUSH R4
    for i in range(6):
        for opcode,address in ((0xF2,p['timers'][i%2]),(0x02,p['delta']+2*i),(0xF6,0xE200+2*i)):
            stub += bytes((opcode,0xF4,address&255,address>>8))
    stub += bytes.fromhex('FCF4FB88')  # POP R4; RETI

    def seed(state):
        emu = _seed(gate,candidate,p,trim=[0,64,128,129,192,255],state=state,status=0xA1)
        for i,t in enumerate((0,64,128,129,192,255)):
            emu.write(p['delta']+2*i,(_corrected(2000,t)-2000)&65535)
        for address in p['timers']:
            emu.write(address,2100)
        for i,byte in enumerate(stub):
            emu.write_byte(0xE100+i,byte)
        emu.write(0xFF98,0x44)
        return emu

    count=0
    masked=[]
    for state in (0xA1,0xA0):
        probe=seed(state)
        trace=[]
        probe.cpu.set_trace(lambda pc,op:trace.append((pc,probe.reg.psw.IEN,probe.cpu.oscillator_cycles)))
        steps=_call(probe,p['base']).steps
        new=[(probe.read(p['timers'][i%2])+probe.read(p['delta']+2*i))&65535 for i in range(6)]
        old=[_corrected(2000,t)+100 for t in (0,64,128,129,192,255)]
        begin=None
        for index,(_,ien,clock) in enumerate(trace):
            if not ien and begin is None:begin=(index,clock)
            if ien and begin is not None:
                masked.append({'instructions':index-begin[0],'normalized_time_units':clock-begin[1]});begin=None
        for boundary in range(1,steps):
            emu=seed(state)
            emu.interrupts.schedule_irq('ADC',after=boundary)
            _call(emu,p['base'])
            assert len(emu.interrupts.entries)==1,(version,state,boundary)
            seen=[emu.read(0xE200+2*i) for i in range(6)]
            assert seen in (old,new),(version,state,boundary,seen,old,new)
            assert emu.reg.psw.IEN and emu.reg.psw.ILVL==0
            count+=1

    # Actual current-state changes during unmasked preparation force fallback.
    for address,value,width in ((0xFA74,32000,2),(0xFA80,1,1),(0xD801,0xA0,1),
            (p['flags'],0,2),(0xFA81,1,1)):
        emu=seed(0xA1)
        changed=[False]
        def change(pc,op):
            # Public trace injection declares one asynchronous engine-state
            # transition. It never supplies a computed result or CPU behavior.
            if not changed[0] and emu.reg.psw.IEN and pc>p['base']+200 and pc<p['base']+400:
                changed[0]=True
                (emu.write_byte if width==1 else emu.write)(address,value)
        emu.cpu.set_trace(change)
        _call(emu,p['base'])
        assert changed[0] and emu.read_byte(0xD80C)==0xA2,(version,address)
        assert _values(emu,p)[0]==[500,600]
        count+=1
    METRICS[version]={'irq_boundary_cases':count-5,'masked_sections':masked,
        'max_masked_instructions':max(r['instructions'] for r in masked),
        'max_masked_normalized_units':max(r['normalized_time_units'] for r in masked)}
    return count


def verify_fixed_ipw(gate,version,installed_image):
    """Run accepted/fallback/native/transition/whole-caller contracts."""
    p = LAYOUTS[version]
    reference = gate._cut_reference(version)
    count = 0
    # Relocated native tail is byte-for-byte stock, including real arithmetic
    # calls and relative branches. Its return adds only the enclosing CALLS.
    from pathlib import Path
    family = version.lower().replace('.','')
    raw = bytes.fromhex((Path(__file__).parent/f'ignition_cut_v18_{family}_fuel.hex').read_text())
    assert installed_image[p['base']^0x4000:(p['base']^0x4000)+len(raw)] == raw
    assert raw[-146:-2] == reference[p['hook']^0x4000:p['end']^0x4000]
    assert raw[-2:] == bytes.fromhex('DB00')
    table={'MS41.0':0x28C8,'MS41.1':0x2BE2,'MS41.2':0x2BCE,'MS41.3':0x2BCE}[version]^0x4000
    assert reference[table:table+6]==bytes((0,1,0,1,0,1))

    # All six trims are checked and all six deltas are published together;
    # the selected native channel cannot conceal another cylinder's overflow.
    for trim in (0,64,128,129,192,255):
        limit = 40000//8-100-1
        boundary = min(65534,((limit+1)*4096-1)//(2048+16*trim))
        for ipw in sorted({0,1,1000,boundary,boundary+1,43738,65534}):
            image = _tuned(gate,installed_image,p,ipw)
            for cylinder in (0,1,5):
                emu = _seed(gate,image,p,trim=trim,cylinder=cylinder,dpp0=11)
                gpr = list(emu.reg.r)
                _call(emu,p['base'])
                corrected = _corrected(ipw,trim)
                accepted = corrected<=limit
                assert emu.read_byte(0xD80C) == (0xA1 if accepted else 0xA2), (version,trim,ipw)
                banks,timers,deltas = _values(emu,p)
                if accepted:
                    assert banks == [ipw,ipw] and timers == [ipw+100]*2
                    assert deltas == [(corrected-ipw)&65535]*6
                    assert all(0 < (timer+deltas[i])&65535 < 5000 for i,timer in enumerate(timers*3))
                else:
                    assert banks == [500,600] and timers == [600,700]
                    assert deltas[cylinder] == (_corrected(banks[cylinder%2],trim)-banks[cylinder%2])&65535
                assert all(emu.reg.r[i] == gpr[i] for i in (0,1,2,3,6,7,8,9,10,11,14,15))
                assert emu.read(p['prev']) == 400 and emu.read(p['avg']) == 500
                count += 1

    # Explicit disqualifiers never publish a fixed bank. Undefined modes are
    # rejected before the raw period can be interpreted as duration ticks.
    invalid = ((p['flags'],0),(p['flags'],0x2400),(p['flags'],0x8400),
        (0xFD02,8),(0xFF50,0x4240),(0xFF50,0),(0xFF46,0x6040),
        (0xFF46,0x60C1),(0xFA74,0),(0xFA74,800),(0xFE46,40000))
    image = _tuned(gate,installed_image,p,1000)
    for address,value in invalid:
        emu = _seed(gate,image,p)
        emu.write(address,value)
        _call(emu,p['base'])
        assert emu.read_byte(0xD80C)==0xA2 and _values(emu,p)[0]==[500,600], (hex(address),value)
        count += 1
    for address,value in ((0xFA81,1),(0xFA80,6),(0xFA80,255)):
        emu = _seed(gate,image,p)
        emu.write_byte(address,value)
        _call(emu,p['base'])
        assert emu.read_byte(0xD80C)==0xA2
        count += 1
    for index in (6,253,255):
        emu = _seed(gate,image,p,cylinder=index)
        _call(emu,p['base'])
        assert emu.read_byte(0xD80C)==0xA2
        count += 1
    for latency,expected in ((0,0xA2),(1,0xA1),(4999,0xA1),(5000,0xA2),(65535,0xA2)):
        emu = _seed(gate,_tuned(gate,installed_image,p,0),p,latency=latency)
        _call(emu,p['base'])
        assert emu.read_byte(0xD80C)==expected,(latency,hex(emu.read_byte(0xD80C)))
        count += 1

    # Disabled and FFFF paths preserve the native tail's architecture and
    # stores. LC's FFFF intentionally wins over standalone's finite value.
    for state,ipw,launch in ((0xA0,1000,1000),(0xA4,1000,1000),
            (0xA1,65535,1000),(0xA3,1000,65535)):
        image = _tuned(gate,installed_image,p,ipw,launch)
        for dpp in (4,11):
            current = _seed(gate,image,p,state=state,dpp0=dpp,trim=64)
            stock = _seed(gate,reference,p,state=state,dpp0=dpp,trim=64)
            _call(current,p['base'])
            _run(stock,p['hook'],p['end'])
            assert _values(current,p)==_values(stock,p)
            assert list(current.reg.r)==list(stock.reg.r)
            assert current.reg.psw.pack()==stock.reg.psw.pack()
            assert (current.reg.mdl,current.reg.mdh,current.reg.mdc)==(stock.reg.mdl,stock.reg.mdh,stock.reg.mdc)
            assert current.read_byte(0xD80C)==0xA0
            count += 1
    for state,expected in ((0xA2,1800),(0xA3,1800),(0xA9,1000),(0xAB,1800)):
        emu = _seed(gate,_tuned(gate,installed_image,p,1000,1800),p,state=state)
        _call(emu,p['base'])
        assert emu.read_byte(0xD80C)==0xA1 and _values(emu,p)[0]==[expected]*2
        count += 1

    # Existing accepted override is coherently replaced by the newly native
    # banks at release/rejection. The native producer owns those current banks.
    for state,ipw,status in ((0xA0,1000,0xA0),(0xA1,65534,0xA2),(0xA1,65535,0xA0)):
        trims = [0,64,128,129,192,255]
        emu = _seed(gate,_tuned(gate,installed_image,p,ipw),p,trim=trims,state=state,status=0xA1)
        _call(emu,p['base'])
        banks,timers,deltas = _values(emu,p)
        assert banks==[500,600] and timers==[600,700]
        assert deltas==[(_corrected(banks[i%2],trims[i])-banks[i%2])&65535 for i in range(6)]
        assert emu.read_byte(0xD80C)==status
        count += 1

    # Native caller entry through its actual RETS, using distinct R0/SP. The
    # ordinary calculated banks win at rejection; acceptance updates all6.
    for ipw,expected_status in ((1000,0xA1),(65534,0xA2),(65535,0xA0)):
        image=gate._full_stack_calibrations(installed_image,gate._bound_variant(installed_image),
            {'CUTSW':0,'CUTRPM':125,'CUT_HYST':0,'CUT_IPW':ipw,'LC_SW':255,'LC_IPW':65535})
        emu=_seed(gate,image,p)
        emu.write_byte(p['rpm'],160)
        emu.write_byte(0xE8D0,128)
        emu.write_byte(0xFC9D,137)
        emu.write(0xE8E4,4096)
        for channel in range(10):
            emu.write(0xFA94+2*channel,(channel<<12)|(0x100+channel*32))
        visited=[]
        emu.cpu.set_trace(lambda pc,op:visited.append(pc))
        _call(emu,p['task'])
        assert all(pc in visited for pc in (p['hook'],p['control'],p['base'],p['end']))
        assert emu.read_byte(0xD80C)==expected_status
        if ipw==1000:
            assert _values(emu,p)==([1000,1000],[1100,1100],[0]*6)
        else:
            assert _values(emu,p)[0]!=[ipw,ipw]
        count+=1

    # Retained outputs pass unchanged through the actual next native consumer.
    # Supplied arrival/context, not a claim of a complete crank lifecycle.
    for stage,mask,transient,inhibit in ((0,0,0,False),(6,0,0,False),
            (0,63,0,False),(0,0,63,False),(0,1,0,False),(0,0,0,True)):
        emu=_seed(gate,_tuned(gate,installed_image,p,1000),p,trim=[0,64,128,129,192,255])
        _call(emu,p['base'])
        values=_consumer(emu,p,version,stage,mask,transient,inhibit)
        if stage or mask==63 or transient==63 or inhibit:
            assert values==[0xDEAD]*6,(version,stage,mask,transient,inhibit,values)
        else:
            expected=[1000+100+_corrected(1000,t) for t in (0,64,128,129,192,255)]
            if mask:expected[0]=0xDEAD
            assert values==expected,(version,values,expected)
        count+=1
    # Six supplied successive native selector phases, retaining the previous
    # transaction's RAM. Every foreground entry produces new native banks
    # before the late owner; every release recalculates all six corrections.
    image=gate._full_stack_calibrations(installed_image,gate._bound_variant(installed_image),
        {'CUTSW':0,'CUTRPM':125,'CUT_HYST':0,'CUT_IPW':1000,'LC_SW':255,'LC_IPW':65535})
    emu=_seed(gate,image,p,trim=[0,64,128,129,192,255])
    emu.write_byte(0xE8D0,128)
    emu.write_byte(0xFC9D,137)
    emu.write(0xE8E4,4096)
    for channel in range(10):
        emu.write(0xFA94+2*channel,(channel<<12)|(0x100+channel*32))
    for phase in range(6):
        active=phase%2==0
        emu.write_byte(p['rpm'],160 if active else 100)
        emu.write_byte(p['index'],phase)
        emu.write_byte(0xFA80,phase)
        if version=='MS41.3':
            # SS1v2's request owner runs at the real CC6 charge call, not in
            # foreground fueling. Preserve that native ordering explicitly.
            _run(emu,0x3D92A,0x3D92E)
        captured=[]
        emu.cpu.set_trace(lambda pc,op:captured.append(tuple(emu.read(a) for a in p['banks'])) if pc==p['base'] else None)
        _call(emu,p['task'])
        assert len(captured)==1
        banks,timers,deltas=_values(emu,p)
        assert banks==([1000]*2 if active else list(captured[0]))
        assert deltas==[(_corrected(banks[i%2],t)-banks[i%2])&65535 for i,t in enumerate((0,64,128,129,192,255))]
        assert emu.read_byte(0xD80C)==(0xA1 if active else 0xA0)
        count+=1
    # Exact historical bytes are a behavioral negative control: the same
    # permitted raw calibration wraps the native timer+delta sum to zero.
    suffix='' if version=='MS41.3' else '_'+family
    legacy,_=gate.patch_ms41.build(reference,['ignition_cut_v17'+suffix,'launch_control_v17'+suffix],allow_deprecated=True)
    legacy=gate._bind_image(legacy,gate._bound_variant(reference))
    legacy=gate._full_stack_calibrations(legacy,gate._bound_variant(reference),
        {'CUTSW':0,'CUTRPM':125,'CUT_HYST':0,'CUT_IPW':43738,'LC_SW':255,'LC_IPW':65535})
    prior=_seed(gate,legacy,p,trim=255)
    prior.write_byte(p['rpm'],160)
    _run(prior,p['hook'],p['end'])
    assert (prior.read(p['timers'][0])+prior.read(p['delta']))&65535==0
    current=_seed(gate,_tuned(gate,installed_image,p,43738),p,trim=255)
    _call(current,p['base'])
    assert current.read_byte(0xD80C)==0xA2 and _values(current,p)[0]==[500,600]
    count+=1
    count+=_preemption(gate,version,_tuned(gate,installed_image,p,1000),p)
    return count
