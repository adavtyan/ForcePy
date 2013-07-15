import random, os, json
import numpy as np
import numpy.linalg as ln
from math import ceil
from MDAnalysis import Universe
from scipy import weave
from scipy.weave import converters
from math import *
import matplotlib.mlab as mlab
import matplotlib.pyplot as plt
from ForcePy.util import *
from ForcePy.ForceCategories import *


class ForceMatch:
    """Main force match class.
    """
    
    def __init__(self, cguniverse, input_file):
        self.ref_cats = []
        self.tar_cats = []
        self.ref_forces =  []
        self.tar_forces = []
        self.u = cguniverse
        self._load_json(input_file) 
        self.force_match_calls = 0
        self.plot_frequency = 1
        self.plot_output = None
    
    def _load_json(self, input_file):
        with open(input_file, 'r') as f:
            self.json = json.load(f)
        self._test_json(self.json, [])
        self.kt = self.json["kT"]
        if("observable" in self.json):
            self.do_obs = True
            self.obs = [0 for x in range(self.u.trajectory.numframes)]
            with open(self.json["observable"], 'r') as f:
                lines = f.readlines()
                if(len(lines) < len(self.obs)):
                    raise IOError("Number of the frames (%d) does not match number of lines in observation file (%d)" %
                                  (len(self.obs), len(lines)))
                for i, line in zip(range(len(self.obs)), lines[:len(self.obs)]):
                    self.obs[i] = float(line.split()[0])
            if("observable_set" in self.json):
                self.obs = np.apply_along_axis(lambda x:(x - self.json["observable_set"]) ** 2, 0, self.obs)
                print "setting observable to %g" % self.json["observable_set"]

        if("box" in self.json):
            if(len(self.json["box"]) != 3):
                raise IOError("Input file JSON: box must look like \"box\":[5,5,5]. It must have 3 dimensions in an array")

                
                
    def _test_json(self, json, required_keys = [("structure", "Toplogy file"), ("trajectory", "Trajectory File"), ("kT", "Boltzmann's constant times temperature")]):
        for rk in required_keys:
            if(not json.has_key(rk[0])):
                raise IOError("Error in input file, could not find %s\n. Set using %s keyword" % (rk[1], rk[0]))

    def add_tar_force(self, *forces):
        for f in forces:
            self.tar_forces.append(f)
            cat = f.get_category()
            if(not (cat is None)):
                self.tar_cats.append(cat)
            f.setup_hook(self.u)

    def add_ref_force(self, *forces):
        for f in forces:
            self.ref_forces.append(f)
            cat = f.get_category()
            if(not (cat is None)):
                self.ref_cats.append(cat)
            f.setup_hook(self.u)


    def swap_match_parameters_cache(self):
        try:
            for f in self.tar_forces:
                self.cache[f], f.lip = f.lip, self.cache[f]
        except AttributeError:
            self.cache = {}
            for f in self.tar_forces:
                self.cache[f] = np.copy(f.lip)
                        
        
    def force_match(self, iterations = 0):
        
        if(iterations == 0):
            iterations = self.u.trajectory.numframes
        
        ref_forces = np.zeros( (self.u.atoms.numberOfAtoms(), 3) )
        self.u.trajectory.rewind() # just in case this is called after some analysis has been done
        
        #setup plots
        if(self.plot_frequency != -1):

            plot_fig = plt.figure()

            #try to maximize the window
            mng = plt.get_current_fig_manager()
            try:
                mng.frame.Maximize(True)
            except AttributeError:
                try:
                    mng.resize(*mng.window.maxsize())
                except AttributeError:
                    pass


            if(self.plot_output is None):
                plt.ion()                        
            #set-up plots for 16/9 screen
            plot_w = ceil(sqrt(len(self.tar_forces)) * 4 / 3.)
            plot_h = ceil(plot_w * 9. / 16.)
            for i in range(len(self.tar_forces)):
                self.tar_forces[i].plot(plt.subplot(plot_w, plot_h, i+1))
            plt.show()
                

        for ts in self.u.trajectory:
            
            #set box if necessary
            if("box" in self.json):
                #strange ordering due to charm
                self.u.trajectory.ts._unitcell[0] = self.json["box"][0]
                self.u.trajectory.ts._unitcell[2] = self.json["box"][1]
                self.u.trajectory.ts._unitcell[5] = self.json["box"][2]

            self._setup()

            for rf in self.ref_forces:
                rf.calc_forces(ref_forces, self.u)            

            #make plots
                if(self.plot_frequency != -1 and iterations % self.plot_frequency == 0):
                    for f in self.tar_forces:
                        f.update_plot()
                    plt.draw()

            #track error
            net_df = 0
            self.force_match_calls += 1

            #sample particles and run updates on them 
            for i in random.sample(range(self.u.atoms.numberOfAtoms()),self.u.atoms.numberOfAtoms()):
                #calculate net forces deviation
                df = ref_forces[i]                
                for f in self.tar_forces:
                    df -= f.calc_particle_force(i,self.u)
                net_df += ln.norm(df)


                #inline C code to accumulate gradient
                code = """
                       for(int i = 0; i < w_length; i++) {
                           grad(i) = 0;
                           for(int j = 0; j < 3; j++)
                               grad(i) -= temp_grad(i,j) * df(j); //negative due to df being switched
                       }
                """

                #now run gradient update step on all the force types
                for f in self.tar_forces:
                    #setup temps for inlince C code
                    w_length = len(f.w)
                    grad = f.w_grad
                    temp_grad = f.temp_grad
                    weave.inline(code, ['w_length', 'grad', 'df', 'temp_grad'],
                         type_converters=converters.blitz,
                         compiler = 'gcc')

                    #the code which is being weaved:
                    #grad = np.apply_along_axis(np.sum, 1, self.temp_grad * df)

                    #apply any regularization
                    for r in f.regularization:
                        grad += r[0](f.w)
                    f.lip +=  np.square(grad)

                    f.w = f.w - f.eta / np.sqrt(f.lip) * grad

            ref_forces.fill(0)
            self._teardown()

            #log of the error
            print "log error at %d  = %g" % (iterations, 0 if net_df < 1 else log(net_df))
            
            iterations -= 1
            if(iterations == 0):
                break
        if(not self.plot_output is None):
            plot_fig.tight_layout()
            plt.savefig(self.plot_output)


    def observation_match(self, obs_sweeps = 25, obs_samples = None, reject_tol = None):
        """ Match observations
        """

        if(obs_samples is None):
            obs_samples = max(5, self.u.trajectory.numframes / obs_sweeps)
        if(reject_tol is None):
            reject_tol = obs_samples
                
        #in case force mathcing is being performed simultaneously,
        #we want to cache any force specific parameters so that we
        #can swap them back in afterwards
        self.swap_match_parameters_cache()

        #we're going to sample the covariance using importance
        #sampling. This requires a normalization coefficient, so
        #we must do multiple random frames

        s_grads = {} #this is to store the sampled gradients. Key is Force and Value is gradient
        for f in self.tar_forces:
            s_grads[f] = [None for x in range(obs_samples)]
        s_obs = [0 for x in range(obs_samples)] #this is to store the sampled observations
        s_weights = [0 for x in range(obs_samples)]

        if(self.plot_output is None):
            plt.ion()                        
            #set-up plots for 16/9 screen
        plot_w = ceil(sqrt(len(self.tar_forces)) * 4 / 3.)
        plot_h = ceil(plot_w * 9. / 16.)
        for i in range(len(self.tar_forces)):
            self.tar_forces[i].plot(plt.subplot(plot_w, plot_h, i+1))
        plt.show()

            
        for s in range(obs_sweeps):

            self.force_match_calls += 1
            #make plots
            for f in self.tar_forces:
                for f in self.tar_forces:
                    f.update_plot()
                plt.draw()

            #now we esimtate gradient of the loss function via importance sampling
            normalization = 0
                
            #note, this reading method is so slow. I should implement the frame jump in xyz
            rejects = 0
            i = 0

            while i < obs_samples:

                index = self._sample_ts() #sample a trajectory frame
                self._setup()

                 #get weight
                dev_energy = 0
                for f in self.tar_forces:
                    dev_energy -= f.calc_potentials(self.u)
                        
                for f in self.ref_forces:
                    dev_energy += f.calc_potentials(self.u)

                    
                if(abs(dev_energy /self.kt) > 250):
                    rejects += 1
                    if(rejects == reject_tol):
                        print "Rejection rate of frames is too high, restarting force matching"
                        self.swap_match_parameters_cache()
                        self.force_match(rejects) #arbitrarily using number of rejects for number matces to use
                        self.swap_match_parameters_cache()
                        rejects = 0
                        continue
                    else:
                        continue

                weight = exp(dev_energy / self.kt)
                s_weights[i] = weight
                normalization += weight

                #store gradient and observabels
                s_obs[i] = weight * self.obs[i]
                for f in self.tar_forces:
                    s_grads[f][i] = weight * np.copy(f.temp_grad[:,1])

                i += 1
                self._teardown()

            #normalize and calculate covariance
            for f in self.tar_forces:
                f.w_grad.fill(0)
                grad = f.w_grad

                #two-pass covariance calculation, utilizing the temp_grad in f
                meanobs = sum(s_obs) / normalization
                meangrad = f.temp_grad[:,2]
                meangrad.fill(0)
                for x in s_grads[f]:
                    meangrad += x  / normalization
                for x,y in zip(s_obs, s_grads[f]):
                    grad += (x - meanobs) * (y - meangrad) / normalization

                #recall we need the negative covariance times the inverse temperature
                grad *= -1. / self.kt

                #now update the weights
                f.lip += np.square(grad)
                change = f.eta / np.sqrt(f.lip) * grad
                f.w = f.w - f.eta / np.sqrt(f.lip) * grad

                print "Obs Mean: %g, reweighted mean: %g" % (sum(self.obs) / len(self.obs) ,meanobs)

            

    def add_and_type_pair(self, force):
        types = []
        for a in self.u.atoms:
            if(not a.type in types):
                types.append(a.type)
        for i in range(len(types)):
            for j in range(i,len(types)):
                if(force.category.pair_exists(self.u, 'type %s' % types[i], 'type %s' % types[j])):
                    f = force.clone_force()
                    f.specialize_types(types[i], types[j])
                    self.add_tar_force(f)

    def _sample_ts(self):        
        self.u.trajectory.rewind()
        index = random.randint(0,self.u.trajectory.numframes - 1)
        [self.u.trajectory.next() for x in range(index)]
        return index
                   

        
            
    def _setup(self):
        for rfcat in self.ref_cats:
            rfcat._setup(self.u)
        for tfcat in self.tar_cats:
            tfcat._setup_update(self.u)        

    def _teardown(self):
        for rfcat in self.ref_cats:
            rfcat._teardown()
        for tfcat in self.tar_cats:
            tfcat._teardown_update()        


    def write_lammps_tables(self, file_name, points=1000):
        
        with open(file_name, 'w') as of:
            for rf in self.tar_forces:
                rf.write_lammps_table(of, points)
                of.write("\n\n")
        
        
    def prepare_lammps(self,  prefix='', folder = os.curdir, table_points=1000):
        if(not os.path.exists(folder)):
            os.mkdir(folder)
        os.chdir(folder)
        
        #write force tables
        write_lammps_tables('%sforce_tables.table' % prefix, table_points)

        with open('%s_fm.data' % prefix) as lf:
            
            #comment header
            lf.write('Generated by ForcePy\n')

            #force field header
            pairs = []
            bonds = []
            for f in self.tar_forces:
                
                try:
                    if(type(f.category) == Pairwise):
                        pairs.append(f)
                    if(type(f.category) == Bond):
                        bonds.append(f)
                except AttributeError:
                    pass
            
            #types
            types = []
            for a in self.u.atoms:
            if(not a.type in types):
                types.append(a.type)

            
            lf.write('%d atom types' % len(types))
            lf.write('%d bond types' % len(bonds))
                    
