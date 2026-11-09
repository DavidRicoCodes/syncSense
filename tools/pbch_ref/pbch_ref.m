% Reference tables and test vectors for a Python PBCH/BCH decoder (no radio).
K = 56; E = 864; N = 512;
Genc = zeros(K, N);
rng(3); r0 = randi([0 1], K, 1); x0 = double(nrPolarEncode(r0, E));
for i = 1:K
    u = r0; u(i) = 1 - u(i);                     % unit vectors alone return a scalar
    Genc(i,:) = mod(double(nrPolarEncode(u, E)) + x0, 2).';
end
Rm = zeros(N, E);
rng(4); y0 = randi([0 1], N, 1); z0 = double(nrRateMatchPolar(y0, K, E));
for j = 1:N
    x = y0; x(j) = 1 - x(j);
    Rm(j,:) = mod(double(nrRateMatchPolar(x, K, E)) + z0, 2).';
end
rng(7);
T = 300;
mib = randi([0 1], 24, T); sfn = randi([0 1023], 1, T); hrf = randi([0 1], 1, T);
ncell = randi([0 1007], 1, T); ibar = randi([0 7], 1, T); kssb = randi([0 1], 1, T);
cw = zeros(E, T); sym = zeros(432, T); crcin = zeros(K, T);
for t = 1:T
    cw(:,t) = nrBCH(mib(:,t), sfn(t), hrf(t), 8, kssb(t), ncell(t));
    sym(:,t) = nrPBCH(cw(:,t), ncell(t), ibar(t));
end
% decode check with MATLAB itself on the clean symbols
okm = 0;
for t = 1:T
    llr = nrSymbolDemodulate(sym(:,t), 'QPSK', 1e-3);
    rx = nrPBCHDecode(sym(:,t), ncell(t), ibar(t), 1e-3);
    [~, err, trblk, lsbofsfn, h, msb] = nrBCHDecode(rx, 8, 8, ncell(t));
    okm = okm + (err == 0 && isequal(trblk, mib(:,t)));
end
fprintf('MATLAB self-check %d/%d\n', okm, T);
ind = nrPBCHIndices(0);                          % 0-based linear indices in a 240x4 grid
ind1 = nrPBCHIndices(1);
save('pbch_ref.mat', 'Genc', 'Rm', 'mib', 'sfn', 'hrf', 'ncell', 'ibar', 'kssb', 'cw', 'sym', 'ind', 'ind1', '-v7');
disp('saved');
