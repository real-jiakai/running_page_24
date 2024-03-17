interface ISiteMetadataResult {
  siteTitle: string;
  siteUrl: string;
  description: string;
  logo: string;
  navLinks: {
    name: string;
    url: string;
  }[];
}

const data: ISiteMetadataResult = {
  siteTitle: 'Running Page',
  siteUrl: 'https://run.gujiakai.top',
  logo: 'https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQTtc69JxHNcmN1ETpMUX4dozAgAN6iPjWalQ&usqp=CAU',
  description: 'Running Page',
  navLinks: [
    {
      name: 'Blog',
      url: 'https://blog.gujiakai.top',
    },
    {
      name: 'About',
      url: 'https://blog.gujiakai.top/about',
    },
    {
      name: '2019~2023',
      url: 'https://running.gujiakai.top',
    }
  ],
};

export default data;
