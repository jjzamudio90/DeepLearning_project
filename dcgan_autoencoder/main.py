from __future__ import print_function
import argparse
import os
import random
import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.optim as optim
import torch.utils.data
import torchvision.datasets as dset
import torchvision.transforms as transforms
import torchvision.utils as vutils
from torch.autograd import Variable


parser = argparse.ArgumentParser()
parser.add_argument('--dataset', required=True, help='cifar10 | lsun | imagenet | folder | lfw | fake')
parser.add_argument('--dataroot', required=True, help='path to dataset')
parser.add_argument('--workers', type=int, help='number of data loading workers', default=2)
parser.add_argument('--batchSize', type=int, default=64, help='input batch size')
parser.add_argument('--imageSize', type=int, default=64, help='the height / width of the input image to network')
parser.add_argument('--nz', type=int, default=100, help='size of the latent z vector')
parser.add_argument('--ngf', type=int, default=64)
parser.add_argument('--ndf', type=int, default=64)
parser.add_argument('--niter', type=int, default=25, help='number of epochs to train for')
parser.add_argument('--lr', type=float, default=0.0002, help='learning rate, default=0.0002')
parser.add_argument('--beta1', type=float, default=0.5, help='beta1 for adam. default=0.5')
parser.add_argument('--cuda', action='store_true', help='enables cuda')
parser.add_argument('--ngpu', type=int, default=1, help='number of GPUs to use')
parser.add_argument('--netG', default='', help="path to netG (to continue training)")
parser.add_argument('--netD', default='', help="path to netD (to continue training)")
parser.add_argument('--outf', default='.', help='folder to output images and model checkpoints')
parser.add_argument('--manualSeed', type=int, help='manual seed')

parser.add_argument('--pretrain_epochs', type=int, default=5, help='number of epochs to pre-train for')

opt = parser.parse_args()
print(opt)

try:
    os.makedirs(opt.outf)
except OSError:
    pass

if opt.manualSeed is None:
    opt.manualSeed = random.randint(1, 10000)
print("Random Seed: ", opt.manualSeed)
random.seed(opt.manualSeed)
torch.manual_seed(opt.manualSeed)
if opt.cuda:
    torch.cuda.manual_seed_all(opt.manualSeed)

cudnn.benchmark = True

if torch.cuda.is_available() and not opt.cuda:
    print("WARNING: You have a CUDA device, so you should probably run with --cuda")

if opt.dataset in ['imagenet', 'folder', 'lfw']:
    # folder dataset
    dataset = dset.ImageFolder(root=opt.dataroot,
                               transform=transforms.Compose([
                                   transforms.Resize(opt.imageSize),
                                   transforms.CenterCrop(opt.imageSize),
                                   transforms.ToTensor(),
                                   
                               ]))
elif opt.dataset == 'lsun':
    dataset = dset.LSUN(root=opt.dataroot, classes=['bedroom_train'],
                        transform=transforms.Compose([
                            transforms.Resize(opt.imageSize),
                            transforms.CenterCrop(opt.imageSize),
                            transforms.ToTensor(),
                            
                        ]))
elif opt.dataset == 'cifar10':
    trainSet = dset.CIFAR10(root=opt.dataroot, train=True, download=True,
                           transform=transforms.Compose([
                               transforms.Resize(opt.imageSize),
                               transforms.ToTensor(),
                               
                           ]))
    pretrainSet = dset.CIFAR10(root=opt.dataroot, train=False, download=True,
                           transform=transforms.Compose([
                                                         transforms.Resize(opt.imageSize),
                                                         transforms.ToTensor(),
                                                         
                                                         ]))

elif opt.dataset == 'fake':
    dataset = dset.FakeData(image_size=(3, opt.imageSize, opt.imageSize),
                            transform=transforms.ToTensor())
assert trainSet
assert pretrainSet

#split dataset into 3 parts
pretrainloader = torch.utils.data.DataLoader(pretrainSet, batch_size=opt.batchSize,
                                         shuffle=True, num_workers=int(opt.workers))

DSet = [trainSet[i] for i in range(25000)]
GSet = [trainSet[i] for i in range(25000,50000)]
Dloader = torch.utils.data.DataLoader(DSet, batch_size=opt.batchSize,
                                         shuffle=True, num_workers=int(opt.workers))
Gloader = torch.utils.data.DataLoader(GSet, batch_size=opt.batchSize,
                                         shuffle=True, num_workers=int(opt.workers))

#dataloader = torch.utils.data.DataLoader(dataset, batch_size=opt.batchSize,
#                                      shuffle=True, num_workers=int(opt.workers))

ngpu = int(opt.ngpu)
nz = int(opt.nz)
ngf = int(opt.ngf)
ndf = int(opt.ndf)
nc = 3


# custom weights initialization called on netG and netD
def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        m.weight.data.normal_(0.0, 0.02)
    elif classname.find('BatchNorm') != -1:
        m.weight.data.normal_(1.0, 0.02)
        m.bias.data.fill_(0)


class _netG(nn.Module):
    def __init__(self, ngpu):
        super(_netG, self).__init__()
        self.ngpu = ngpu
        self.main = nn.Sequential(
            # input is Z, going into a convolution
#            nn.ConvTranspose2d(     nz, ngf * 8, 4, 1, 0, bias=False),
#            nn.BatchNorm2d(ngf * 8),
#            nn.ReLU(True),
            # state size. (ngf*8) x 4 x 4
            nn.ConvTranspose2d(ngf * 8, ngf * 4, 4, 2, 1, bias=False),
            nn.BatchNorm2d(ngf * 4),
            nn.ReLU(True),
            # state size. (ngf*4) x 8 x 8
            nn.ConvTranspose2d(ngf * 4, ngf * 2, 4, 2, 1, bias=False),
            nn.BatchNorm2d(ngf * 2),
            nn.ReLU(True),
            # state size. (ngf*2) x 16 x 16
            nn.ConvTranspose2d(ngf * 2,     ngf, 4, 2, 1, bias=False),
            nn.BatchNorm2d(ngf),
            nn.ReLU(True),
            # state size. (ngf) x 32 x 32
            nn.ConvTranspose2d(    ngf,      nc, 4, 2, 1, bias=False),
            nn.Sigmoid()
            #nn.Tanh()
            # state size. (nc) x 64 x 64
        )

    def forward(self, input):
        if isinstance(input.data, torch.cuda.FloatTensor) and self.ngpu > 1:
            output = nn.parallel.data_parallel(self.main, input, range(self.ngpu))
        else:
            output = self.main(input)
        return output


netG = _netG(ngpu)
netG.apply(weights_init)
if opt.netG != '':
    netG.load_state_dict(torch.load(opt.netG))
print(netG)



class _netD(nn.Module):
    def __init__(self, ngpu):
        super(_netD, self).__init__()
        self.ngpu = ngpu
        self.main = nn.Sequential(
            # input is (nc) x 64 x 64
            nn.Conv2d(nc, ndf, 4, 2, 1, bias=False),
            nn.LeakyReLU(0.2, inplace=True),
            # state size. (ndf) x 32 x 32
            nn.Conv2d(ndf, ndf * 2, 4, 2, 1, bias=False),
            nn.BatchNorm2d(ndf * 2),
            nn.LeakyReLU(0.2, inplace=True),
            # state size. (ndf*2) x 16 x 16
            nn.Conv2d(ndf * 2, ndf * 4, 4, 2, 1, bias=False),
            nn.BatchNorm2d(ndf * 4),
            nn.LeakyReLU(0.2, inplace=True),
            # state size. (ndf*4) x 8 x 8
            nn.Conv2d(ndf * 4, ndf * 8, 4, 2, 1, bias=False),
            nn.BatchNorm2d(ndf * 8),
            nn.LeakyReLU(0.2, inplace=True),
            # state size. (ndf*8) x 4 x 4
            nn.Conv2d(ndf * 8, 1, 4, 1, 0, bias=False),
                                  
        
            nn.Sigmoid()
        )

    def forward(self, input):
        if isinstance(input.data, torch.cuda.FloatTensor) and self.ngpu > 1:
            output = nn.parallel.data_parallel(self.main, input, range(self.ngpu))
        else:
            output = self.main(input)

        return output.view(-1, 1).squeeze(1)


netD = _netD(ngpu)
netD.apply(weights_init)
if opt.netD != '':
    netD.load_state_dict(torch.load(opt.netD))
print(netD)

class _AutoEncoder(nn.Module):
    def __init__(self, ngpu):
        super(_AutoEncoder, self).__init__()
        self.ngpu = ngpu
        self.input_to_hidden = nn.Sequential(
             # input is (nc) x 64 x 64
             nn.Conv2d(nc, ndf * 2, 6, 4, 1, bias=False),
             nn.BatchNorm2d(ndf * 2),
             nn.LeakyReLU(0.2, inplace=True),
             # state size. (ndf) x 32 x 32
             #              nn.Conv2d(ndf, ndf * 2, 4, 2, 1, bias=False),
             #              nn.BatchNorm2d(ndf * 2),
             #              nn.LeakyReLU(0.2, inplace=True),
             # state size. (ndf*2) x 16 x 16
             #              nn.Conv2d(ndf * 2, ndf * 4, 4, 2, 1, bias=False),
             #              nn.BatchNorm2d(ndf * 4),
             #              nn.LeakyReLU(0.2, inplace=True),
             # state size. (ndf*4) x 8 x 8
             
             # state size. (ndf*2) x 16 x 16
             nn.Conv2d(ndf * 2, ndf * 8, 6, 4, 1, bias=False),
             nn.BatchNorm2d(ndf * 8),
             nn.Sigmoid()
             # state size. (ndf*8) x 4 x 4
             
             )
        self.hidden_to_output = nn.Sequential(
           # state size. (ngf*8) x 4 x 4
           nn.ConvTranspose2d(ngf * 8, ngf * 2, 6, 4, 1, bias=False),
           nn.BatchNorm2d(ngf * 2),
           nn.ReLU(True),
           # state size. (ngf*4) x 8 x 8
           #              nn.ConvTranspose2d(ngf * 4, ngf * 2, 4, 2, 1, bias=False),
           #              nn.BatchNorm2d(ngf * 2),
           #              nn.ReLU(True),
           #              # state size. (ngf*2) x 16 x 16
           #              nn.ConvTranspose2d(ngf * 2,     ngf, 4, 2, 1, bias=False),
           #              nn.BatchNorm2d(ngf),
           #              nn.ReLU(True),
           # state size. (ngf) x 32 x 32
           nn.ConvTranspose2d(    ngf * 2,      nc, 6, 4, 1, bias=False),
           
           nn.Sigmoid()
           # state size. (nc) x 64 x 64
           )

    def forward(self, input):
        hidden = self.input_to_hidden(input)
        output = self.hidden_to_output(hidden)
        return output

AutoEncoder = _AutoEncoder(ngpu)
print(AutoEncoder)

criterion_ae = nn.BCELoss()

input = torch.FloatTensor(opt.batchSize, 3, opt.imageSize, opt.imageSize)

if opt.cuda:
    AutoEncoder.cuda()
    criterion_ae.cuda()
    input = input.cuda()


# setup optimizer
optimizerAE = optim.Adam(AutoEncoder.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))

for epoch in range(opt.pretrain_epochs):
    for i, data in enumerate(pretrainloader, 0):
        ############################
        # (1) Update AutoEncoder network: minimize -(X_in * T.log(X_hat) + (1 - X_in) * T.log(1 - X_hat))
        ###########################

        AutoEncoder.zero_grad()
        real_cpu, _ = data
        batch_size = real_cpu.size(0)
        if opt.cuda:
            real_cpu = real_cpu.cuda()
        input.resize_as_(real_cpu).copy_(real_cpu)
        #input = input/255 # divide by 255 so pixels are in [0,1] range like ouput of autoencoder
        inputv = Variable(input)
        
        output = AutoEncoder(inputv)
        errAE = criterion_ae(output, inputv)
        errAE.backward()
        #AE_x = output.data.mean()
        optimizerAE.step()


criterion = nn.BCELoss() #nn.BCEWithLogitsLoss more numerically stable

inputD = torch.FloatTensor(opt.batchSize, 3, opt.imageSize, opt.imageSize)
inputG = torch.FloatTensor(opt.batchSize, 3, opt.imageSize, opt.imageSize)
noise = torch.FloatTensor(opt.batchSize, nz, 1, 1)
fixed_noise = torch.FloatTensor(opt.batchSize, nz, 1, 1).normal_(0, 1)
label = torch.FloatTensor(opt.batchSize)
real_label = 1
fake_label = 0

if opt.cuda:
    netD.cuda()
    netG.cuda()
    criterion.cuda()
    inputD, inputG, label = inputD.cuda(), inputG.cuda(), label.cuda()
    noise, fixed_noise = noise.cuda(), fixed_noise.cuda()

fixed_noise = Variable(fixed_noise)

# setup optimizer
optimizerD = optim.Adam(netD.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))
optimizerG = optim.Adam(netG.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))

skipD = False
skipG = False
countD = 0
countG = 0
#D_x = 0
D_G_z1 = 0
D_G_z2 = 0

D_G_list = []
G_losses = []

for epoch in range(opt.niter):
    #for i, data in enumerate(dataloader, 0):
    for (i, data_d), (j, data_g) in zip(enumerate(Dloader, 0), enumerate(Gloader, 0)):
        ############################
        # (1) Update D network: maximize log(D(x)) + log(1 - D(G(z)))
        ###########################
       
       # train with real
        if not skipD:
            netD.zero_grad()
            real_cpu_d, _ = data_d
            real_cpu_g, _ = data_g
            batch_size_d = real_cpu_d.size(0)
            batch_size_g = real_cpu_g.size(0)
            if opt.cuda:
                real_cpu_d = real_cpu_d.cuda()
                real_cpu_g = real_cpu_g.cuda()
            inputD.resize_as_(real_cpu_d).copy_(real_cpu_d)
            inputG.resize_as_(real_cpu_g).copy_(real_cpu_g)
            label.resize_(batch_size_d).fill_(real_label)
            inputdv = Variable(inputD)
            inputgv = Variable(inputG)
            labelv = Variable(label)

            output = netD(inputdv)
            errD_real = criterion(output, labelv)
            errD_real.backward()
            D_x = output.data.mean()

            # train with fake
    #        noise.resize_(batch_size, nz, 1, 1).normal_(0, 1)
    #        noisev = Variable(noise)
            ae_input = AutoEncoder.input_to_hidden(inputgv) #trained autoencoder to take data in [0,1]
            fake = netG(ae_input)
            #labelv = Variable(label.fill_(fake_label))
            label.resize_(batch_size_g).fill_(fake_label)
            labelv = Variable(label)
            output = netD(fake.detach())
            errD_fake = criterion(output, labelv)
            errD_fake.backward()
            D_G_z1 = output.data.mean()
            errD = errD_real + errD_fake
            optimizerD.step()
            
            skipD = False
            skipG = False
            if countD < 1 and i > 0 and ( (D_x) < 0.505 or (D_x - D_G_z1) < 0):
                skipG = True
                skipD = False
                countD = 1
            else:
                countD = 0

        ############################
        # (2) Update G network: maximize log(D(G(z)))
        ###########################
        
        if not skipG:
            if skipD:
                real_cpu_g, _ = data_g
                batch_size_g = real_cpu_g.size(0)
                if opt.cuda:
                    real_cpu_g = real_cpu_g.cuda()
                inputG.resize_as_(real_cpu_g).copy_(real_cpu_g)
                inputgv = Variable(inputG)
                label.resize_(batch_size_g).fill_(real_label)
                ae_input = AutoEncoder.input_to_hidden(inputgv) #trained autoencoder to take data in [0,1]
                fake = netG(ae_input)
                fake = fake.detach()
        
            netG.zero_grad()
            labelv = Variable(label.fill_(real_label))  # fake labels are real for generator cost
            output = netD(fake)
            errG = criterion(output, labelv)
            errG.backward()
            D_G_z2 = output.data.mean()
            optimizerG.step()

            skipD = False
            skipG = False
            if countG < 1 and (D_x - D_G_z2) > 0.65 and D_G_z1 < 0.3:
                skipD = True
                skipG = False
                countG = 1
            else:
                countG = 0

        print('[%d/%d][%d/%d] Loss_D: %.4f Loss_G: %.4f D(x): %.4f D(G(z)): %.4f / %.4f'
              % (epoch, opt.niter, i, len(Dloader),
                 errD.data[0], errG.data[0], D_x, D_G_z1, D_G_z2))

        if i % 5 == 0:
            D_G_list.append(D_G_z1)
            G_losses.append(errG.data[0])
        if i % 100 == 0:
            vutils.save_image(real_cpu,
                    '%s/real_samples.png' % opt.outf,
                    normalize=True)
            fake = netG(ae_input) #netG(fixed_noise)
            vutils.save_image(fake.data,
                    '%s/fake_samples_epoch_%03d.png' % (opt.outf, epoch),
                    normalize=True)

    # do checkpointing
    torch.save(netG.state_dict(), '%s/netG_epoch_%d.pth' % (opt.outf, epoch))
    torch.save(netD.state_dict(), '%s/netD_epoch_%d.pth' % (opt.outf, epoch))

#Save final images for t-SNE
import numpy as np
mydata= (fake.data).cpu().numpy()
#print(mydata.shape)
#(16, 3, 64, 64)
mydata=np.array([mydata[i].flatten() for i in range(mydata.shape[0])])
np.savetxt('%s/fake_images%d_ae%d.csv' % (opt.outf, epoch,opt.pretrain_epochs),mydata,delimiter=",")

#Real images
mydata=(real_cpu).cpu().numpy()
mydata=np.array([mydata[i].flatten() for i in range(mydata.shape[0])])
np.savetxt('%s/real_images%d_ae%d.csv' % (opt.outf, epoch,opt.pretrain_epochs),mydata,delimiter=",")

#save D_G
D_G_array = np.array(D_G_list)
np.savetxt('%s/D_G_z%d_ae%d.csv' % (opt.outf, epoch,opt.pretrain_epochs),D_G_array,delimiter=",")

G_losses_array = np.array(G_losses)
np.savetxt('%s/G_losses%d_ae%d.csv' % (opt.outf, epoch,opt.pretrain_epochs),G_losses_array,delimiter=",")


