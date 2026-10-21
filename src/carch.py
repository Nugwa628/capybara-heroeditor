import struct, zlib, marshal, sys
COOKIE=b'MEI\x0c\x0b\x0a\x0b\x0e'
def read(path):
    d=open(path,'rb').read()
    i=d.rfind(COOKIE)
    magic,pkglen,tocoff,toclen,pyver,pylib=struct.unpack('!8sIIii64s',d[i:i+88])
    start=i+88-pkglen
    toc=d[start+tocoff:start+tocoff+toclen]
    ents=[];p=0
    while p<len(toc):
        elen,off,clen,ulen,cflag,typ=struct.unpack('!IIIIBc',toc[p:p+18])
        name=toc[p+18:p+elen].rstrip(b'\0').decode()
        ents.append(dict(name=name,off=off,clen=clen,ulen=ulen,cflag=cflag,typ=typ.decode(),data=d[start+off:start+off+clen]))
        p+=elen
    return d,start,ents,(pyver,pylib)
if __name__=='__main__':
    d,start,ents,info=read(sys.argv[1])
    print('pkg start',start, info[0])
    for e in ents: print(e['typ'],e['cflag'],e['clen'],e['ulen'],e['name'])
