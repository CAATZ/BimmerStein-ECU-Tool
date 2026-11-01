"""Focused current installed fixed-IPW admission; private reference opt-in."""
import os
from pathlib import Path

import pytest


@pytest.mark.parametrize('version',('MS41.0','MS41.1','MS41.2','MS41.3'))
def test_fixed_ipw_native_envelope_and_handoffs(version):
    if not all(Path(os.environ.get(name,'__missing__')).is_dir()
               for name in ('MS41EMU_ROOT','MS41_TEST_DATA_ROOT')):
        pytest.skip('canonical emulator and exact private reference images required')
    from engines.patcher import verify_ms412_emulator as gate
    from engines.patcher.verify_fixed_ipw import verify_fixed_ipw
    suffix='' if version=='MS41.3' else '_'+version.lower().replace('.','')
    reference=gate._cut_reference(version)
    image,_=gate.patch_ms41.build(reference,['ignition_cut_v18'+suffix,'launch_control_v18'+suffix])
    image=gate._bind_image(image,gate._bound_variant(reference))
    assert verify_fixed_ipw(gate,version,image)>700
