% 200 ms of an SSB-only NR signal (30 kHz SCS, 15.36 Msps, SSB at DC), SSB every 20 ms,
% SFN incrementing by 2 per SSB. For an offline end-to-end check of the Python PBCH chain.
ncell = 0; ibar = 0; sfn0 = 1018; nper = 12;
carrier = nrCarrierConfig('SubcarrierSpacing', 30, 'NSizeGrid', 20);
wave = []; sfns = zeros(1, nper);
for p = 1:nper
    sfn = mod(sfn0 + 2*(p-1), 1024); sfns(p) = sfn;
    mib = [0; int2bit(floor(sfn/16), 6); 1; zeros(4,1); 0; int2bit(73, 8); 1; 1; 0];
    cw = nrBCH(mib, sfn, 0, 8, 0, ncell);
    g = zeros(240, 4);
    g(nrPSSIndices) = nrPSS(ncell); g(nrSSSIndices) = nrSSS(ncell);
    g(nrPBCHIndices(ncell)) = nrPBCH(cw, ncell, ibar);
    g(nrPBCHDMRSIndices(ncell)) = nrPBCHDMRS(ncell, ibar);
    for s = 0:39
        slot = zeros(240, 14);
        if s == 0, slot(:, 3:6) = g; end
        carrier.NSlot = s;
        wave = [wave; nrOFDMModulate(carrier, slot, 'Nfft', 512, 'SampleRate', 15.36e6)]; %#ok<AGROW>
    end
end
save('ssb_wave.mat', 'wave', 'sfns', 'ncell', '-v7');
fprintf('samples %d (%.1f ms)\n', numel(wave), numel(wave)/15.36e3);
