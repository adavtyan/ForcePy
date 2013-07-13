from MDAnalysis import Universe
from ForcePy import *
import numpy as np


    
cgu = CGUniverse(Universe("test/water/spc.tpr", "test/water/traj.trr"), ['name OW', 'name HW1 or name HW2'], ['O', 'H2'], collapse_hydrogens=False)
cgu.add_residue_bonds("name O", "name H2")
fm = ForceMatch(cgu, "test/water/spc_cg.json")
ff = FileForce()
pwf = SpectralForce(Pairwise, UniformMesh(0,10,0.05), Basis.UnitStep)
bwf = SpectralForce(Bond, UniformMesh(0,1,0.002), Basis.UnitStep)

pwf.add_regularizer(SmoothRegularizer)
bwf.add_regularizer(SmoothRegularizer)
fm.add_ref_force(ff)
fm.add_and_type_pair(pwf)
fm.add_and_type_pair(bwf)
fm.force_match(5)
fm.observation_match(obs_sweeps = 3, obs_samples = 5)